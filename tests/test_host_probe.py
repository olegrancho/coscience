"""O5: the onboarding probe reads a server over key-only SSH and proposes an entry."""
import pytest

from coscience import host_probe
from coscience.host_probe import (SSH_OPTIONS, parse_facts, probe_host, propose, remote_path,
                                  ssh_argv, warnings_for)
from tests.host_probe_fakes import SAMPLE_OUTPUT, FakeRunner


def test_ssh_argv_accepts_an_alias_a_user_and_a_port():
    assert ssh_argv("gpu1") == ["ssh", *SSH_OPTIONS, "gpu1"]
    assert ssh_argv("me@box.example") == ["ssh", *SSH_OPTIONS, "me@box.example"]
    assert ssh_argv("me@box:2222") == ["ssh", *SSH_OPTIONS, "-p", "2222", "me@box"]


@pytest.mark.parametrize("target", ["", "-oProxyCommand=x", "a b", "me@-x", "box;rm", "me@box:port"])
def test_ssh_argv_refuses_anything_else(target):
    with pytest.raises(ValueError, match="ssh target"):
        ssh_argv(target)


def test_ssh_options_never_prompt_and_never_accept_new_host_keys():
    assert "BatchMode=yes" in SSH_OPTIONS
    assert not any("StrictHostKeyChecking" in o for o in SSH_OPTIONS)


def test_remote_path_expands_home_on_the_server_and_quotes_the_rest():
    assert remote_path("~/coscience-runs") == '"$HOME"/coscience-runs'
    assert remote_path("~/runs dir") == "\"$HOME\"/'runs dir'"
    assert remote_path("/data/runs") == "/data/runs"


def test_parse_facts_reads_the_probe_output():
    facts = parse_facts(SAMPLE_OUTPUT, now=990.0)
    assert facts["threads"] == 12 and facts["cores"] == 6
    assert facts["mem_total_kb"] == 65000000
    assert facts["gpus"] == [{"index": 0, "model": "Example GPU 11GB", "vram_gb": 10.8,
                              "driver": "460.39"}]
    assert facts["tools"]["rsync"] == "/usr/bin/rsync" and facts["tools"]["conda"] == ""
    assert facts["clock_offset_s"] == 10
    assert facts["internet"] is True
    assert facts["disk_used_pct"] == 97


def test_propose_offers_all_threads_or_half_on_a_shared_server():
    facts = parse_facts(SAMPLE_OUTPUT, now=1000.0)
    assert propose(facts, shared=False) == {
        "capacity": {"cpu": 12.0, "memory_gb": 55.0},
        "gpus": [{"model": "Example GPU 11GB", "vram_gb": 10.8}]}
    assert propose(facts, shared=True)["capacity"]["cpu"] == 6.0


def test_warnings_name_what_will_bite():
    facts = parse_facts(SAMPLE_OUTPUT, now=1000.0)
    warnings = warnings_for(facts, [], shared=False)
    text = "\n".join(warnings)
    assert "97% full" in text
    assert "glibc 2.17" in text
    assert "CUDA 11.2" in text
    assert "4 users are logged in" in text


def test_a_probe_runs_the_script_and_every_check():
    runner = FakeRunner()
    result = probe_host("gpu1", runner=runner, now=1000.0)
    assert result["ok"] is True and result["error"] == ""
    assert [c["name"] for c in result["checks"]] == [
        "key-only SSH", "run root writable", "detached job survives", "rsync both ways"]
    assert all(c["ok"] for c in result["checks"])
    assert result["proposal"]["capacity"]["cpu"] == 12.0
    assert runner.calls[0][-2:] == ["bash", "-s"]
    assert any("rm -rf" in call[-1] for call in runner.calls)              # scratch cleaned up


def test_an_unknown_host_key_is_explained():
    runner = FakeRunner({"probe": (255, "", "Host key verification failed.\r\n")})
    result = probe_host("gpu1", runner=runner, now=1000.0)
    assert result["ok"] is False
    assert "connect once by hand" in result["error"]
    assert len(runner.calls) == 1                                          # no checks after a failed login


def test_a_refused_key_is_explained():
    runner = FakeRunner({"probe": (255, "", "me@box: Permission denied (publickey).\r\n")})
    assert "public key" in probe_host("gpu1", runner=runner, now=1000.0)["error"]


def test_a_failed_check_becomes_a_warning():
    runner = FakeRunner({"alive": (1, "", "")})
    result = probe_host("gpu1", runner=runner, now=1000.0)
    detached = next(c for c in result["checks"] if c["name"] == "detached job survives")
    assert detached["ok"] is False
    assert any(w.startswith("check failed: detached job survives") for w in result["warnings"])


@pytest.mark.parametrize("run_root", ["~/runs;rm -rf /", "~/runs dir", "runs", "~/a/../b", "$(id)", "~user/runs",
                                      "~/.", "~/./", "/.", "~/a//b", "~/a/./b", "//", "///"])
def test_a_run_root_outside_the_safe_form_is_refused_before_login(run_root):
    runner = FakeRunner()
    with pytest.raises(ValueError, match="run root"):
        probe_host("gpu1", run_root, runner=runner, now=1000.0)
    assert runner.calls == []


def test_a_safe_run_root_reaches_rsync_as_written():
    runner = FakeRunner()
    probe_host("gpu1", "/data/coscience-runs/", runner=runner, now=1000.0)
    rsync = [c for c in runner.calls if c[0] == "rsync"]
    assert rsync[0][-1] == "gpu1:/data/coscience-runs/.coscience-probe/rsync-test"


def test_subprocess_runner_reports_success_a_missing_program_and_a_timeout():
    assert host_probe.subprocess_runner(["true"], None, 5)[0] == 0
    assert host_probe.subprocess_runner(["coscience-no-such-program"], None, 5)[0] == 127
    assert host_probe.subprocess_runner(["sleep", "5"], None, 0.1)[0] == 124


def test_scratch_is_cleaned_up_even_when_a_check_raises():
    class Exploding(FakeRunner):
        def __call__(self, argv, stdin, timeout):
            if argv[0] == "rsync":
                self.calls.append(list(argv))
                raise RuntimeError("boom")
            return super().__call__(argv, stdin, timeout)
    runner = Exploding()
    with pytest.raises(RuntimeError):
        host_probe.run_checks(runner, "gpu1", "~/coscience-runs")
    assert any("rm -rf" in c[-1] for c in runner.calls)


def test_a_card_with_unreadable_vram_is_proposed_as_a_whole_card():
    output = SAMPLE_OUTPUT.replace("gpu=0|Example GPU 11GB|11019|460.39",
                                   "nvidia_smi=present\ngpu=0|Example GPU|[N/A]|470.00")
    facts = parse_facts(output, now=1000.0)
    assert facts["gpus"] == [{"index": 0, "model": "Example GPU", "vram_gb": None, "driver": "470.00"}]
    proposal = propose(facts, shared=False)
    assert proposal["gpus"] == [] and proposal["capacity"]["gpu"] == 1.0
    assert any("VRAM could not be read" in w for w in warnings_for(facts, [], shared=False))


def test_nvidia_smi_that_reports_nothing_is_warned_about():
    output = SAMPLE_OUTPUT.replace("gpu=0|Example GPU 11GB|11019|460.39", "nvidia_smi=present")
    facts = parse_facts(output, now=1000.0)
    assert any("reported no GPUs" in w for w in warnings_for(facts, [], shared=False))


def test_internet_is_unknown_without_curl_and_not_warned_about():
    facts = parse_facts(SAMPLE_OUTPUT.replace("internet=yes", "internet=unknown"), now=1000.0)
    assert facts["internet"] is None
    assert not any("internet" in w for w in warnings_for(facts, [], shared=False))
