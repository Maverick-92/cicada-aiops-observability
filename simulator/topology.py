import hmac, hashlib, json
SALT = b"fleet-sim-enrollment-salt"
COUNTRIES = [
    ("India", "IN", "Asia/Kolkata", ["Bengaluru", "Mumbai", "Delhi", "Chennai"]),
    ("Japan", "JP", "Asia/Tokyo", ["Tokyo", "Osaka", "Nagoya", "Fukuoka"]),
    ("China", "CN", "Asia/Shanghai", ["Shanghai", "Beijing", "Shenzhen", "Guangzhou"]),
    ("Germany", "DE", "Europe/Berlin", ["Frankfurt", "Berlin", "Munich", "Hamburg"]),
    ("USA", "US", "America/New_York", ["Ashburn", "Dallas", "Chicago", "San Jose"]),
]

def build():
    out = []
    for country, cc, tz, cities in COUNTRIES:
        for city in cities:
            for b in range(1, 4):
                site = cc + "-" + city.replace(" ", "")[:3].upper() + "-B" + str(b)
                for s in range(1, 11):
                    host = site + "-srv" + str(s).zfill(2)
                    h = "srv_" + hmac.new(SALT, host.encode(), hashlib.sha256).hexdigest()[:12]
                    out.append({"source_hash": h, "site_id": site, "country": country, "city": city, "building": "B" + str(b), "source_tz": tz, "server_no": s})
    return out

if __name__ == "__main__":
    t = build()
    assert len(t) == 600, len(t)
    assert len({x["source_hash"] for x in t}) == 600, "hash collision"
    json.dump(t, open("simulator/topology.json", "w"), indent=1)
    print("topology.json written:", len(t), "servers,", len({x["site_id"] for x in t}), "sites")
