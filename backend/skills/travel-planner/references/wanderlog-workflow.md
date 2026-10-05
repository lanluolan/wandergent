# Wanderlog Workflow

Use this reference when the user wants the trip created or updated in Wanderlog after the plan is clear.

## Core rule

Plan first, write second.

Do not start by spraying tools across Wanderlog before the itinerary logic is settled. Research and optimize the trip in chat, get alignment, then execute.

## Tool selection
### Trip discovery and creation

- `wanderlog_list_trips`
  Use when the user mentions a trip by name and you need to find the correct `trip_key`.

- `wanderlog_get_trip`
  Use before editing an existing trip so you understand the current structure, dates, and what already exists.

- `wanderlog_create_trip`
  Use when the user wants a brand-new trip created in Wanderlog.

- `wanderlog_update_trip_dates`
  Use when the trip exists but the user wants the date range changed.
- `wanderlog_get_trip_url`
  Use when the user wants an edit, view, or suggest link after planning.
### Destination and place discovery

- `wanderlog_search_places`
  Use when a user request is vague and you need candidate places near the trip destination before choosing what to add.

- `wanderlog_add_place`
  Use to add attractions, restaurants, cafes, viewpoints, neighborhoods, and other stops to a trip day or the unscheduled list.
### Practical itinerary context

- `wanderlog_add_note`
  Use after places to add practical context:
  - how to get there
  - what to book in advance
  - local advice
  - timing warnings
  - food ordering tips
  - rain / backup suggestions

Do not batch all notes at the end of the day. Add them in sequence so the itinerary reads naturally.
### Accommodation and preparation

- `wanderlog_add_hotel`
  Use for the primary stay once dates and base neighborhood are known. On broad multi-city or country-wide trips, be ready for stricter resolution behavior than `wanderlog_search_places`.

- `wanderlog_add_checklist`
  Use for:
  - pre-trip checklist
  - packing reminders
  - reservation tasks
  - day-specific prep items

### Cleanup and maintenance

- `wanderlog_remove_place`
  Use to remove outdated or unwanted stops from an existing trip.
### Budget and expense tracking

- `wanderlog_list_expenses`
  Use to inspect current tracked expenses.

- `wanderlog_get_budget_summary`
  Use when the user wants a spending overview by category.

- `wanderlog_add_expense`
  Use only for actual budget entries the user wants recorded.

- `wanderlog_delete_expense`
  Use to remove incorrect or duplicate expense entries.

## Recommended execution order
### New trip
1. `wanderlog_create_trip`
2. `wanderlog_add_hotel` if the property resolves cleanly; on broad multi-city trips, hotel resolution may fail even when place search succeeds
3. Add day-by-day places with `wanderlog_add_place`
4. After each place or stop cluster, add `wanderlog_add_note`
5. Make sure any ticketed stop has fee or reservation context already surfaced in chat, and add the practical fee / booking reminder to the relevant note when useful
6. If transport is unusually costly or pass-based, surface that in chat first and add the practical transit-cost reminder to the relevant note when useful
7. If a restaurant is a real itinerary anchor, surface its cost / booking context in chat first and add the practical dining note when useful
8. Add `wanderlog_add_checklist` for pre-trip prep and important day-specific tasks
9. Add expenses only if the user wants real budget tracking
### Existing trip

1. `wanderlog_list_trips` if needed
2. `wanderlog_get_trip`
3. `wanderlog_update_trip_dates` if necessary
4. Add / remove / update content using the relevant tools
5. Return the trip URL if useful
## Day construction guidance

For each day you write into Wanderlog:

- respect the traveler's natural start time
- cluster stops by area
- keep the number of anchors realistic for the stated pace
- separate ambitious anchors with enough transit and recovery time
- protect meal windows
- reduce midday outdoor exposure in hot / high-crowd seasons when possible
- keep arrival and departure days lighter
- include fallback or caution guidance when weather or queues could disrupt the day
## Notes that add real value

Good Wanderlog notes should include practical travel value, not generic summary prose. Every written day should end up with at least one note after each place or stop cluster.

Strong examples:
- metro / walking / taxi guidance between stops
- whether tickets sell out and how early to book
- what time to arrive to avoid queues
- what to order or what is special at a restaurant
- whether a restaurant needs a reservation or has a notable cost level
- whether a stop works as a rainy-day backup
- cultural etiquette that matters at that stop
## What to avoid

- adding places without practical notes
- writing a fully packed schedule for a relaxed traveler
- ignoring seasonal crowd or weather constraints
- adding ticketed attractions without price / booking context already established
- adding expenses based on guesses instead of user intent
- promising a hotel write before confirming the property actually resolves
- modifying an existing trip without reading it first
