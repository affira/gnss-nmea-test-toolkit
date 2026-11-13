# Prompt 4: README, prompts folder and privacy check

Write a README covering:
- what the toolkit does and why
- how to run it and its tests
- an example report generated from one of the real gpsd logs, with the actual numbers from running it (keep the thresholds labelled as illustrative examples)
- the design decisions
- honest limitations: tested on recorded logs only, not live hardware, and NMEA only (no u-blox UBX binary protocol)
- a section called "Lessons learned and next steps"

Credit every data source with its licence. Add a prompts/ folder with the prompts I used, including this one.

For "Lessons learned and next steps": base it only on what actually happened in this project: decisions we made, bugs or edge cases the tests caught, what the real gpsd logs showed versus the synthetic ones, and the limits of testing recorded logs without hardware. Write in plain first person only for decisions I actually approved in this session; otherwise use neutral wording. Do not invent experiences, feelings or results. End with 3–4 concrete next steps for testing on real receiver hardware. Keep this section under 250 words.

Show me the full README before committing.

Finally, check the repo for anything private (local paths, names of my other projects, API keys or tokens) before I push.
