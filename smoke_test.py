
import os, json, sys
sys.path.insert(0, "/workspace/fleetfoot")
os.environ["DB_PATH"] = "/workspace/fleetfoot/test.db"
if os.path.exists(os.environ["DB_PATH"]): os.remove(os.environ["DB_PATH"])
import app as A
A.init_db()
c = A.app.test_client()

# fee math
with A.app.app_context():
    for m in (1.0, 3.0, 3.2, 5.0, 7.4):
        print("  ", m, "mi ->", A.money(A.fee_for_miles(m)))

# customer order using a cached demo address (works offline)
r = c.post("/api/quote", json={"restaurant_id":1, "address":"2302 Waverly Parkway, Opelika, AL 36801"})
print("quote:", r.json)
r = c.post("/checkout", json={"restaurant_id":1, "customer_name":"John W", "customer_phone":"3347079069",
      "address":"2302 Waverly Parkway, Opelika, AL 36801", "note":"front door",
      "items":[{"id":1,"name":"Tiger Burger","price_cents":1099,"qty":2}]})
print("checkout:", r.json)
code = r.json["code"]

# restaurant accepts + timer + ready
c.post("/restaurant/login", data={"slug":"tigertown","pin":"1111"})
o = c.get("/api/restaurant/orders").json["orders"][0]
c.post("/api/order/status", json={"order_id":o["id"], "kitchen_status":"preparing", "prep_minutes":12})

# with every driver offline the order must sit HELD in the queue
t = c.get("/api/track/"+code).json["order"]
print("no drivers ->", t["dispatch_status"], t["hold_reason"], "queue #", t["queue_position"])

# driver can only REQUEST; status does not change
d = A.app.test_client()
d.post("/driver/login", data={"phone":"3345550111","pin":"1234"})
d.post("/api/driver/request", json={"status":"online"})
st = d.get("/api/driver/state").json["driver"]
print("after driver request ->", st["status"], "| pending:", st["pending_request"])

# dispatcher approves, order auto-assigns
p = A.app.test_client()
p.post("/dispatch/login", data={"username":"admin","password":"dispatch123"})
p.post("/api/dispatch/driver-status", json={"driver_id":1, "status":"online"})
st = d.get("/api/driver/state").json
print("after dispatch approval ->", st["driver"]["status"], "| stack:", len(st["stack"]))

# driver stages: received -> at restaurant -> enroute -> complete
oid = st["stack"][0]["id"]
for stage in ("received","at_restaurant","enroute","delivered"):
    d.post("/api/order/status", json={"order_id":oid, "dispatch_status":stage})
    print("   stage ->", c.get("/api/track/"+code).json["order"]["dispatch_status"])

board = p.get("/api/dispatch/board").json
print("live:", len(board["orders"]), "| completed tab:", [o["code"] for o in board["completed"]])
print("dispatcher can set driver status:", p.post("/api/dispatch/driver-status",
      json={"driver_id":1,"status":"break"}).json)
print("driver status now:", d.get("/api/driver/state").json["driver"]["status"])

# stacking: three orders, one driver, max 3
p.post("/api/dispatch/driver-status", json={"driver_id":1, "status":"online"})
codes=[]
for i in range(3):
    rr = c.post("/checkout", json={"restaurant_id":1, "customer_name":"Cust%d"%i, "customer_phone":"3345551%03d"%i,
        "address":"600 S College St, Auburn, AL 36832", "items":[{"id":2,"name":"Catfish","price_cents":1399,"qty":1}]})
    codes.append(rr.json["code"])
    o = c.get("/api/restaurant/orders").json["orders"][-1]
    c.post("/api/order/status", json={"order_id":o["id"], "kitchen_status":"preparing", "prep_minutes":10})
st = d.get("/api/driver/state").json
print("stacked on one driver:", [(o["code"], o["stack_seq"]) for o in st["stack"]])
print("still queued/held:", [(o["code"], o["dispatch_status"], o["queue_position"]) for o in st["queue"]])
print("HEALTH:", c.get("/healthz").json["ok"])
