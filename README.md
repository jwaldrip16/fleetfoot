# FleetFoot Delivery

A working four-app delivery platform: customer ordering site, dispatcher portal, driver app and
restaurant app. One Flask process, one SQLite file, no build step.

## Run it

    cd fleetfoot
    pip install -r requirements.txt
    TZ=America/Chicago python app.py     # http://localhost:5000

Demo logins

| Surface    | URL                | Credentials                                   |
|------------|--------------------|-----------------------------------------------|
| Customer   | /                  | none                                           |
| Dispatch   | /dispatch          | admin / dispatch123                            |
| Driver     | /driver            | 3345550111 / 1234 (also ...0122, ...0133)      |
| Restaurant | /restaurant        | tigertown / 1111, noodle / 1111, valleybbq / 1111 |

## What each surface does

**Customer site.** Browse open restaurants (open/closed comes from the live hours table), build a
cart, type an address. The address is validated and geocoded before checkout; an address that does
not resolve blocks the order. Tracking page shows kitchen timer, queue position, driver and a
navigation link.

**Dispatcher portal.** Live order board with kitchen status, dispatch status, queue number and hold
reason. Set or clear a driver's status (online, break, off), approve the status a driver requested,
assign or reassign orders, stack several on one driver, start the kitchen timer, mark ready, hold an
order, walk an order through received / at restaurant / enroute / complete, and chat with any driver.
Separate tabs for live orders and completed orders. Restaurant hours and the fee table are editable
under /dispatch/restaurants and /dispatch/settings.

**Driver app.** Drivers cannot flip their own status. They tap Request online / Request break /
Request clock out, or just type it in chat ("going on break"), and it lands with dispatch as a
pending request until dispatch approves it. Their run lists each stop in stack order with turn by
turn navigation to the restaurant and to the customer, stage buttons for received, at restaurant,
enroute and complete, and a live view of the queue with positions. Complete pops a warning first;
after the driver confirms, the order leaves the run and lands in completed orders. Drivers can also
place an order for a walk-up customer at /driver/new-order.

**Restaurant app.** Pending orders arrive, the store accepts with a prep timer (counts down for the
customer, dispatch and the driver), marks ready, and can pause incoming orders.

## Delivery fee

First 3 miles $3.99, then $1.00 per additional mile, rounded up. Distance is restaurant to validated
customer address. All three numbers are editable in /dispatch/settings.

## Queue and holds

Orders with no driver sit in one queue, oldest first, numbered for customer, dispatcher and driver.
If nobody is online, or every online driver is at their stack limit, the order is held with the
reason "no driver available" and promotes itself the moment capacity appears. Auto assign can be
turned off in settings so dispatch places every order by hand. Stack limit is per driver, default 3.

## Address validation and maps

Set GOOGLE_MAPS_API_KEY to use Google Geocoding; with no key it falls back to OpenStreetMap
Nominatim, and demo addresses are cached so it works offline. Distance is great circle times a 1.3
road factor; swap in Distance Matrix for exact road miles. Navigation links open Google Maps
directions, so they work on a phone as well as a desktop.

## Before going live

Replace the plain text PINs and passwords with hashed credentials, put it behind HTTPS, add a real
payment processor at checkout, and move the 4 second polling to websockets if you go past a few
dozen concurrent drivers.
