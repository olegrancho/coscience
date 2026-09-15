"""A canned probe run for onboarding tests: sample output and a fake runner that
answers the ssh and rsync calls `host_probe` makes, without a network."""
from pathlib import Path

SAMPLE_OUTPUT = """hostname=gpu-box
os=Example Linux 9
kernel=5.15.0
arch=x86_64
glibc=2.17
cpu_model=Example CPU
threads=12
sockets=1
cores_per_socket=6
load1=0.12
users=4
mem_total_kb=65000000
mem_available_kb=50000000
swap_total_kb=0
gpu=0|Example GPU 11GB|11019|460.39
cuda=11.2
gpu_processes=0
disk_free_kb=150000000
disk_used_pct=97
tool=rsync|/usr/bin/rsync
tool=setsid|/usr/bin/setsid
tool=nohup|/usr/bin/nohup
tool=python3|/usr/bin/python3
tool=git|/usr/bin/git
tool=conda|
tool=uv|
tool=docker|
tool=apptainer|
tool=claude|
python=3.6.8
internet=yes
boot_id=abc
clock=1000
"""


class FakeRunner:
    """Scripted answers keyed on what each call is doing. `overrides` maps a label
    ("probe", "write", "start", "alive", "rsync", "cleanup") to (code, stdout, stderr)."""

    def __init__(self, overrides=None):
        self.calls: list[list[str]] = []
        self.overrides = dict(overrides or {})
        self._uploaded = ""

    def __call__(self, argv, stdin, timeout):
        self.calls.append(list(argv))
        label = self._label(argv)
        if label in self.overrides:
            return self.overrides[label]
        if label == "probe":
            return 0, SAMPLE_OUTPUT, ""
        if label == "write":
            return 0, "ok\n", ""
        if label == "start":
            return 0, "4242\n", ""
        if label == "alive":
            return 0, "alive\n", ""
        if label == "rsync":
            src, dst = argv[-2], argv[-1]
            if Path(src).is_file():
                self._uploaded = Path(src).read_text()
            else:
                Path(dst).write_text(self._uploaded)
            return 0, "", ""
        return 0, "", ""

    @staticmethod
    def _label(argv):
        if argv[0] == "rsync":
            return "rsync"
        command = argv[-1]
        if argv[-2:] == ["bash", "-s"]:
            return "probe"
        if "touch" in command:
            return "write"
        if "setsid" in command:
            return "start"
        if "kill -0" in command:
            return "alive"
        if "rm -rf" in command:
            return "cleanup"
        return "other"
