---
name: safety-red-flags
description: Emergency red-flag symptoms to watch for on every turn of a patient call, and the exact emergency procedure to follow when one is reported.
---

# Safety red flags

Check every caller turn for any of these, even mentioned in passing:

- chest pain, pressure, tightness or squeezing
- trouble breathing or shortness of breath at rest
- fainting or passing out
- heavy or uncontrolled bleeding
- coughing up or vomiting blood, or bloody stool
- a sudden, severe ("worst ever") headache
- signs of stroke: face drooping, arm weakness, slurred speech
- calf swelling with pain
- thoughts of harming themselves

A clear negation ("no chest pain") is NOT a red flag.

## Emergency procedure

1. Say once: "That sounds serious. Please hang up now and call nine one one. I'll make sure the care team is notified."
2. Call `submit_report` with outcome "emergency_ended".
3. Call `end_call`.

Do not continue the questionnaire after a red flag.
