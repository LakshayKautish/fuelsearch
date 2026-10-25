import json,math,sys
T=json.load(open(sys.argv[1])); OUT=sys.argv[2]
sc,tr=T["transform"]["scale"],T["transform"]["translate"]
def dec(a):
    x=y=0;o=[]
    for dx,dy in a:x+=dx;y+=dy;o.append((x*sc[0]+tr[0],y*sc[1]+tr[1]))
    return o
ARCS=[dec(a) for a in T["arcs"]]
MERGE={"Dadra and Nagar Haveli":"Dadra & Nagar Haveli and Daman & Diu","Daman and Diu":"Dadra & Nagar Haveli and Daman & Diu","Andaman and Nicobar":"Andaman and Nicobar"}
geoms=list(T["objects"].values())[0]["geometries"]
def rings(g):
    if g["type"]=="Polygon":return g["arcs"]
    if g["type"]=="MultiPolygon":return [r for p in g["arcs"] for r in p]
    return []
use={}   # arc -> list of (state, signed index)
for g in geoms:
    st=MERGE.get(g["properties"]["st_nm"],g["properties"]["st_nm"])
    for r in rings(g):
        for a in r:use.setdefault(a if a>=0 else ~a,[]).append((st,a))
def rdp(pts,eps):
    if len(pts)<3:return pts
    keep=[False]*len(pts);keep[0]=keep[-1]=True;st=[(0,len(pts)-1)]
    while st:
        a,b=st.pop();ax,ay=pts[a];bx,by=pts[b];dx,dy=bx-ax,by-ay;L=math.hypot(dx,dy)
        best,bi=0,None
        for i in range(a+1,b):
            px,py=pts[i];d=abs(dy*(px-ax)-dx*(py-ay))/L if L else math.hypot(px-ax,py-ay)
            if d>best:best,bi=d,i
        if bi is not None and best>eps:keep[bi]=True;st+=[(a,bi),(bi,b)]
    return [p for p,k in zip(pts,keep) if k]
EPS=0.02
SIMP={i:rdp(ARCS[i],EPS) for i in use}
K=30;CX=math.cos(math.radians(22))
P=lambda p:f"{(p[0]-67.5)*K*CX:.1f} {(37.5-p[1])*K:.1f}"
def arcpts(a):return SIMP[a] if a>=0 else SIMP[~a][::-1]
# state outlines: arcs used by exactly one district of that state, stitched into rings
states={}
for i,us in use.items():
    by={}
    for st,a in us:by.setdefault(st,[]).append(a)
    for st,lst in by.items():
        if len(lst)==1:states.setdefault(st,[]).append(lst[0])
paths={};npts=0
for st,arcs in states.items():
    pieces=[arcpts(a) for a in arcs];used=[False]*len(pieces);out=[]
    start={}
    for k,p in enumerate(pieces):start.setdefault((round(p[0][0],5),round(p[0][1],5)),[]).append(k)
    for k in range(len(pieces)):
        if used[k]:continue
        ring=list(pieces[k]);used[k]=True
        while True:
            end=(round(ring[-1][0],5),round(ring[-1][1],5))
            nxt=next((j for j in start.get(end,[]) if not used[j]),None)
            if nxt is None:break
            used[nxt]=True;ring+=pieces[nxt][1:]
        # drop tiny islands
        A=abs(sum(ring[i][0]*ring[i-1][1]-ring[i-1][0]*ring[i][1] for i in range(len(ring))))/2
        if (A<(0.00003 if st=="Lakshadweep" else 0.0008)) or len(ring)<4:continue
        npts+=len(ring);out.append("M"+"L".join(P(p) for p in ring)+"Z")
    paths[st]="".join(out)
# borders between states, and the outer line
inner=[];outer=[]
for i,us in use.items():
    sts={s for s,_ in us}
    seg="M"+"L".join(P(p) for p in SIMP[i])
    if len(us)==1:outer.append(seg)
    elif len(sts)>1:inner.append(seg)
res={"states":paths,"inner":"".join(inner),"outer":"".join(outer)}
json.dump(res,open(OUT,"w"),separators=(",",":"))
print(len(paths),"states",npts,"pts; size",len(json.dumps(res)))
