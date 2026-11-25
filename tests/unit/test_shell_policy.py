"""Shell allow-list (requirement 16; Module 7 "command regex filters, path allowlists").

Commands are argv lists (never a shell string), the program must be on the allow-list,
and every path argument must stay inside the sandbox's own mounts.
"""

import pytest

from adpb.sandbox.docker_runner import ShellNotAllowed, ShellPolicy

policy = ShellPolicy()


@pytest.mark.parametrize("argv", [
    ["ls", "-la", "/inputs"],
    ["head", "-n", "5", "/inputs/raw.orders.parquet"],
    ["wc", "-l", "/work/out/x.csv"],
    ["python", "-c", "print(1)"],
])
def test_allowed(argv):
    policy.check(argv)


@pytest.mark.parametrize("argv,why", [
    (["curl", "https://x"], "not on the allow-list"),
    (["rm", "-rf", "/work"], "not on the allow-list"),
    (["sh", "-c", "ls"], "not on the allow-list"),
    (["/bin/ls"], "bare program name"),
    (["cat", "/etc/passwd"], "outside"),
    (["cat", "/inputs/../etc/passwd"], "outside"),
    (["ls", "x; rm -rf /"], "shell metacharacter"),
    (["ls", "$(whoami)"], "shell metacharacter"),
    ([], "empty"),
])
def test_refused(argv, why):
    with pytest.raises(ShellNotAllowed, match=why):
        policy.check(argv)
