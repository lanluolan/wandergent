---
name: travel-planner
description: Travel planning and destination research for realistic, preference-aware trips. Use when planning itineraries, researching destinations, balancing pace, budget, and logistics, creating packing lists, or preparing a trip to be entered into Wanderlog. Especially useful when an AI agent should ask only for missing planning criteria, use live web research for time-sensitive travel facts, produce a plan in chat first, and then translate the result into Wanderlog MCP tool actions.
---
# Travel Planner
## Overview

Use this skill to turn a rough travel idea into a realistic, personalized trip plan. Optimize the trip around the traveler's actual constraints and preferences rather than producing a generic sightseeing list.

This skill is stateless in v1. Do not maintain a local traveler database. Gather what is missing for the current trip, research the destination live, present the plan in chat first, and only then create or update the trip in Wanderlog if the user wants it written there.
When Wanderlog MCP is available, treat this skill as the planning brain and Wanderlog as the execution surface. First produce a coherent plan in chat. Then translate that plan into concrete Wanderlog actions using the workflow in [wanderlog-workflow.md](references/wanderlog-workflow.md).
## Workflow
### 1. Gather essentials first

Collect the minimum trip facts needed to plan:

- destination
- trip dates or date window
- trip length
- traveler count / travel companions
- total budget, daily budget, or hotel budget target
- budget behavior
- trip purpose
- must-do attractions, activities, or foods
- whether the user wants this written into Wanderlog after planning

Ask only for missing information. Do not force a long questionnaire if the user already provided enough context.
Before finalizing a day-by-day itinerary, make sure you have at least: dates / trip length, optimization priority, pace, daily rhythm, actual budget input, and must-do items or constraints. If one of these is still missing and it would materially change the trip, ask before locking the plan.
### 2. Gather optimization criteria

Use the criteria in [planning-criteria.md](references/planning-criteria.md) to decide what makes the trip "good" for this traveler.

Always ask about missing high-impact criteria before building the itinerary. In particular, clarify:
- travel priorities and tradeoffs
- pace preference
- daily rhythm (early bird vs late starter, late nights, slow mornings)
- seasonality sensitivity (crowds, heat, rain, festival season)
- walking / transit tolerance
- paid vs free attraction preference and splurge tolerance
- hotel selection priority (transit convenience, atmosphere, budget, nightlife, quiet)
- food preferences, dietary constraints, and how food-forward the trip should be
- booking style and flexibility
Only ask what matters for the current trip. Use judgment and avoid unnecessary friction.
### Intake priority

When the user gives only a rough travel request, ask in this order and stop once you have enough to plan:
1. dates and trip length
2. what the trip should optimize for most
3. pace preference
4. daily rhythm (early bird vs late starter / late nights)
5. actual budget shape (total, daily, or hotel target) and budget behavior
6. must-do places, foods, or constraints
7. whether they want destination food highlights, practical meal coverage, or both
8. whether they want the final plan written into Wanderlog

Do not ask all seven mechanically if the user already answered some of them.
### 3. Research live destination facts

Use live web research for any fact that can change over time. At minimum, verify the categories in [travel-guidelines.md](references/travel-guidelines.md):
- visa / entry requirements
- weather and seasonality for the exact travel window
- peak season, closures, holidays, or festival impact
- opening hours / closed days for major attractions
- transportation options, typical transfer times, and likely costs for major moves
- current safety or scam concerns
- current pricing when it materially affects the plan
- entrance fees, reservation costs, or timed-entry pricing for paid attractions in the plan
When you infer from the research, say so. Prefer current primary or official sources for high-stakes facts.
### 4. Build a realistic plan

Create an itinerary that matches both the traveler's preferences and the destination realities. Separate clearly between free stops, paid attractions worth recommending, and optional paid upgrades so the user can see where the money goes.

Optimize for:
- realistic timing with buffers
- geographic clustering by neighborhood / region
- crowd and weather avoidance when possible
- meal timing that matches local norms
- fatigue management on arrival, departure, and day-trip days
- backup logic for rain / closure / overruns when useful
- weather fallback and low-energy alternatives when they materially improve the trip
Do not overpack the schedule. If the user prefers a relaxed pace, protect breathing room. If they are late starters, do not place high-friction morning activities too early. If the trip falls in peak season, avoid naive midday bottlenecks when better sequencing exists.

Use the structure and note-writing guidance in [itinerary-style.md](references/itinerary-style.md) to keep the plan realistic and Wanderlog-ready.
### 5. Present the plan in chat first

Present a complete proposal before writing anything to Wanderlog.

Include as relevant:
- trip summary and planning assumptions
- day-by-day itinerary
- transportation notes
- booking notes / urgency flags
- budget breakdown
- paid-attraction costs, entrance fees, and reservation notes for any ticketed stops in the plan
- likely transportation costs for airport transfer, local transit, and any major paid moves that affect the budget
- meal strategy, restaurant cost level, and any reservation-worthy food stops when food matters to the trip
- which recommended stops are free, paid, or optional paid upgrades
- packing checklist
- pre-trip preparation checklist or timeline
Call out any assumptions clearly. If something depends on a user choice, show concise options. Use the structure in [output-template.md](references/output-template.md) unless the user asks for a different format.
### 6. Run a quick plan review before Wanderlog

Before writing the plan into Wanderlog, do a fast self-check:
- too much cross-city transit for the stated pace?
- too many paid stops for the stated budget?
- any expensive transport moves or airport transfers not accounted for?
- too many early starts for the traveler's rhythm?
- any weather-sensitive days without a fallback?
- any meal gaps or awkward meal placement?
- any restaurant anchors missing cost or reservation context?
- any crowd-sensitive anchors placed at a poor time of day?

Fix obvious issues before executing the Wanderlog write.
### 7. Write to Wanderlog after the plan is approved

If the user wants the plan in Wanderlog, use Wanderlog MCP after presenting the plan in chat. Do not start writing to Wanderlog first unless the user explicitly asked for direct trip creation and the plan is already clear.

When using Wanderlog MCP:
- create or identify the correct trip first
- if the trip is country-wide or multi-city, avoid overpromising hotel insertion until the exact property resolves through Wanderlog
- add places, notes, hotels, checklists, and expenses only after the itinerary shape is clear
- be cautious with hotel insertion on broad multi-city or country-wide trips; confirm the property resolves cleanly before promising it can be added
- do not write paid attractions into Wanderlog without surfacing current ticket fees or clearly labeling them as estimated if live pricing was unavailable
- if transport materially affects the plan, surface likely transfer or pass costs in chat before the Wanderlog write
- keep each day to a realistic number of anchors for the traveler's pace
- add at least one practical note after each place or stop cluster
- include reservation or fee context in notes when it affects execution
- interleave places and notes so the itinerary remains usable
- prefer a complete, coherent trip rather than partial disconnected edits
- use the tool sequencing in [wanderlog-workflow.md](references/wanderlog-workflow.md)
If the user asks for the plan to stay in chat only, stop after the written plan.
## Wanderlog-specific behavior

Use Wanderlog intentionally, not mechanically.

### When to use Wanderlog immediately

Use Wanderlog MCP directly after planning when the user clearly wants:

- a new trip created
- an existing trip updated
- places / notes / hotels / checklists added
- dates shifted
- budget items recorded
### When to stay in chat first

Stay in chat first when:

- the destination or date window is still fuzzy
- major tradeoffs are unresolved
- the user asked for ideas, comparison, or research
- the user wants to review the itinerary before committing it
### Planning-to-Wanderlog translation

Translate the approved plan like this:

1. identify or create the trip
2. confirm the date range
3. add the hotel block
4. add each day's places in order
5. add a practical note after each place or stop cluster
6. add pre-trip and day-specific checklists
7. add expenses only when the user gives actual budget items or wants a tracked budget

Do not dump raw research into Wanderlog. Convert it into traveler-usable notes.
### Quality bar for Wanderlog entries

For each planned day:

- keep the stop sequence realistic
- avoid teleporting between far neighborhoods without explanation
- include notes that explain transit, timing, booking, and local tips
- reflect the traveler's wake time, pace, and meal rhythm
- avoid putting fragile reservations too close together

If the user wants edits to an existing trip, inspect the current trip first before mutating it.
## Planning rules
- Ask only for missing high-impact planning criteria.
- Choose itinerary shape based on user pace and season, not a fixed template.
- Treat jet lag, arrival day friction, and travel fatigue as first-class planning constraints.
- Account for peak season, weather, and local operating hours before committing to timing.
- Ask for actual budget shape when missing instead of relying only on a style label.
- Ask about budget behavior instead of assuming cheapest / luxury / balanced.
- Ask whether the traveler prefers mostly free stops, a balanced mix, or a few worthwhile paid attractions when that affects the plan.
- Ask what the hotel should optimize for when accommodation choice matters.
- Do not introduce unsupported certainty for dynamic facts; research them.
- Keep the skill stateless for v1.
- Avoid generic "top 10" trip plans that ignore geography, queue patterns, or daily rhythm.
- Prefer a few well-sequenced anchors plus useful context over an overstuffed checklist.
## Deliverable structure

A strong trip plan usually includes:

1. Trip summary
2. Key assumptions / constraints
3. Day-by-day itinerary
4. Budget outline
5. Paid attraction / entrance fee notes
6. Free vs paid stop guidance
7. Packing notes
8. Booking / prep timeline
9. Optional Wanderlog execution plan
## References

Read these only as needed:
- [planning-criteria.md](references/planning-criteria.md): user criteria that materially change itinerary optimization
- [travel-guidelines.md](references/travel-guidelines.md): live-research checklist and planning heuristics
- [wanderlog-workflow.md](references/wanderlog-workflow.md): tool-by-tool workflow for turning a plan into Wanderlog updates
- [itinerary-style.md](references/itinerary-style.md): day structure, pacing rules, note-writing standards, and weather / meal logic
- [output-template.md](references/output-template.md): reusable response template for presenting a complete trip before Wanderlog execution
