"""The automatic certificate checker: reads a claim (a GitHub issue opened
from one of the claim forms), checks every repo in it the way the
certificates guide says, and reports the result. qbc_check.py is the command
line; course/certificates/README.md explains the whole flow.
"""

# The modules the public claim forms repo gets (publish.py copies them; the
# issuer and publish itself stay in the private repo).
PUBLIC_MODULES = ("__init__", "checks", "claims", "files", "gh", "harness", "items", "pathway",
                  "readme", "report", "repo", "result", "rules", "sandbox", "scan", "sprint", "sweep",
                  "swe", "trusted")
