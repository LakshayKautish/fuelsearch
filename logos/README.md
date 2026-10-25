# Brand logos

Square 128×128 icons used on the list, the station panel and map pins:
ioc.png, hpcl.png, bpcl.png, jiobp.png, nayara.png, shell.png (cropped to the emblem so they read at small sizes).
They are built into index.html, so the site needs no extra image requests.

To swap one without rebuilding, add it to config.js:
    window.FUELSEARCH_CONFIG = { ..., logos: { "IOC": "logos/ioc-new.png" } };
