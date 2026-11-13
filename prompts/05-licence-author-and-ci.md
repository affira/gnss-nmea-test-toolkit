# Prompt 5: licence, commit author and CI

1. README approved. After CI is added, update the Status line to match.
2. Rewrite the author on all commits to my GitHub noreply address: <paste it here>, and set it as this repo's git user.email.
3. Add an MIT licence for my own code (Copyright 2026 Affira), keep gpsd's BSD-2 licence for the data, and add a short Licence section to the README.
4. Go ahead with CI, but don't use `|| true`. Let the sample-report step pass on exit codes 0, 1 and 3, and fail only on 2. Run both pytest and Robot, and upload the Robot report as an artifact.
Show me the final git log and CI file before I push.
