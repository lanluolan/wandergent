# Itinerary Style

Use this reference to produce trip plans that feel realistic, sequenced, and useful in Wanderlog.
## Core principle

A good itinerary is not a list of attractions. It is a believable day.

Each day should have:

- a realistic start time for the traveler
- a small number of meaningful anchors
- logical geography
- meal timing that makes sense locally
- enough buffer for transit, queues, and drift
- practical notes that help the traveler execute the day
- a fallback path if weather or energy changes the day

## Day shape by pace
### Relaxed pace

Default target:

- 2 major anchors, or
- 1 major anchor + neighborhood wandering + one meal destination

Characteristics:

- late or gentle start is okay
- longer meals
- strong buffer time
- room for spontaneous exploration
### Moderate pace

Default target:

- 2 to 3 meaningful anchors
- optional light fourth stop only if geographically easy

Characteristics:

- balanced sightseeing and breathing room
- one anchor in the morning, one in the afternoon, one in the evening if appropriate
- enough slack for queues and transit
### Packed pace

Default target:

- 3 to 4 anchors only if transit is efficient and the traveler wants it

Characteristics:

- earlier starts more acceptable
- tighter routing required
- higher booking and timing discipline

Do not use "packed" as permission to create an impossible day.

## Day-type rules

### Arrival day

- keep the day light
- account for airport transfer and check-in
- assume reduced energy, especially with jet lag
- avoid rigid high-stakes reservations unless arrival timing is safe
### Departure day

- protect checkout and airport / station transfer time
- keep activities close and cancellable
- avoid far day trips or long museum blocks

### Day-trip day

- reduce the number of other anchors
- account for transit fatigue
- avoid forcing a heavy late-night plan afterward unless the traveler wants that
### Weather-sensitive day

- place outdoor queues in the best weather window
- keep indoor backups in mind
- if the day is heavily outdoors, mention at least one practical swap or fallback

## Geography rules

- Cluster by neighborhood, district, or route corridor.
- Avoid backtracking unless there is a strong reason.
- Explain longer moves when they are worth it.
- If two major sights are in different parts of the city, do not pretend the move is frictionless.
## Timing rules

- Respect real opening hours and closed days.
- Respect local meal timing.
- Use queue-aware timing for popular attractions.
- Do not force early mornings on late-rhythm travelers.
- Do not stack timed-entry attractions too tightly.
## Food placement

Food should fit the day, not feel pasted in.

- Use breakfast / brunch only if it matches the traveler's rhythm.
- Lunch should be geographically convenient to the day.
- Dinner should match the evening area or intended vibe.
- For food-focused travelers, meals can be anchors.
- If food is not a major priority, meals should still feel geographically convenient and realistic.
- If a restaurant is a true anchor, note whether it needs booking and what cost level to expect.
## What makes a strong note

A strong note helps the traveler do the trip, not just understand it.

Good note content:

- best way to get from the previous stop
- what time to arrive and why
- what to book in advance
- what is worth ordering / seeing / prioritizing
- local etiquette or practical cautions
- whether the stop works as a rain backup or sunset slot
## What makes a weak note

Weak notes are generic, obvious, or decorative.

Avoid:

- repeating the place name without adding value
- generic praise ("beautiful place", "great atmosphere")
- facts the traveler could infer from the title alone
- large blocks of unstructured prose
## Paid vs free rhythm

A believable day does not stack expensive attractions by accident.

- Make clear which stops are free.
- Keep paid attractions limited enough that the day still fits the user's budget style.
- Use optional paid upgrades when a viewpoint or museum is nice but not essential.
## Anti-slop rules

Avoid these common bad patterns:

- too many iconic landmarks in one day with no transit realism
- every day starting at 8 AM regardless of traveler rhythm
- lunch and dinner inserted without location logic
- no weather, season, or queue awareness
- no distinction between major anchors and filler stops
- notes dumped at the end instead of attached to the flow
## Strong output pattern

For each day, aim for:

1. Day theme or area
2. Realistic start time
3. Main anchors in logical order
4. Meals placed naturally
5. Transit notes between major moves
6. Booking / caution notes where useful
7. Estimated effort and tempo
8. Optional fallback if weather or energy changes
## Wanderlog translation guidance

When writing to Wanderlog:

- use `wanderlog_add_place` for the actual stops
- use `wanderlog_add_note` after each place or stop cluster
- keep note text concise and practical
- prefer a sequence that reads like a day someone could actually follow
