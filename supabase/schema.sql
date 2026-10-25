-- FuelSearch: driver price reports (Supabase / PostgreSQL)
-- Run once in the Supabase SQL editor. The website talks to it with the public anon key only;
-- drivers can add reports through submit_report() and read the summary view, nothing else.

create table if not exists public.station_expected (
  station_id text not null,
  fuel       text not null check (fuel in ('petrol','diesel')),
  price      numeric(6,2) not null,        -- today's official or city price, uploaded by scrape.py
  asof       date not null,
  primary key (station_id, fuel)
);

create table if not exists public.reports (
  id          bigint generated always as identity primary key,
  station_id  text not null,
  fuel        text not null check (fuel in ('petrol','diesel')),
  price       numeric(6,2) not null check (price between 50 and 200),
  dealer_code text check (dealer_code ~ '^[0-9]{5,8}$'),
  device_id   uuid not null,               -- random id kept in the reporter's browser
  status      text not null default 'ok' check (status in ('ok','flagged','removed')),
  created_at  timestamptz not null default now()
);
create index if not exists reports_station_time on public.reports (station_id, fuel, created_at desc);
create index if not exists reports_device_time  on public.reports (device_id, created_at desc);

alter table public.reports          enable row level security;
alter table public.station_expected enable row level security;
-- No policies for anon: the tables are reachable only through the function and view below.

-- Accept a report. Rules:
--   * at most 20 reports per device per day, and 1 per station and fuel per hour
--   * more than ₹3 away from the expected price -> stored as 'flagged' (not shown) for review
create or replace function public.submit_report(p_station text, p_fuel text, p_price numeric,
                                                p_device uuid, p_dealer_code text default null)
returns text language plpgsql security definer set search_path = public as $$
declare exp numeric; st text := 'ok';
begin
  if (select count(*) from reports where device_id = p_device and created_at > now() - interval '1 day') >= 20 then
    return 'limit_day';
  end if;
  if exists (select 1 from reports where device_id = p_device and station_id = p_station and fuel = p_fuel
             and created_at > now() - interval '1 hour') then
    return 'limit_hour';
  end if;
  select price into exp from station_expected where station_id = p_station and fuel = p_fuel;
  if exp is not null and abs(p_price - exp) > 3 then st := 'flagged'; end if;
  insert into reports (station_id, fuel, price, dealer_code, device_id, status)
  values (p_station, p_fuel, round(p_price, 2), nullif(p_dealer_code, ''), p_device, st);
  return st;
end $$;
revoke all on function public.submit_report from public;
grant execute on function public.submit_report to anon, authenticated;

-- What the site reads: today's (since 6:00 AM IST) reports per station and fuel.
create or replace view public.latest_reports with (security_invoker = false) as
select station_id, fuel,
       (array_agg(price order by created_at desc))[1]                       as price,
       count(*)                                                             as reports,
       count(distinct price)                                                as distinct_prices,
       max(created_at)                                                      as last_at
from public.reports
where status = 'ok'
  and created_at >= (date_trunc('day', now() at time zone 'Asia/Kolkata') + interval '6 hours') at time zone 'Asia/Kolkata'
group by station_id, fuel;
grant select on public.latest_reports to anon, authenticated;

-- Dealer codes drivers have typed in: review these and copy confirmed ones into data/stations.csv
create or replace view public.dealer_code_suggestions as
select station_id, dealer_code, count(distinct device_id) as devices, max(created_at) as last_at
from public.reports where dealer_code is not null group by station_id, dealer_code order by devices desc;
