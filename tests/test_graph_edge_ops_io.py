from coscience.models import Program, ProgramStatus
from coscience.pm_agent import read_staging, write_staging
from coscience.pm_claude import parse_response
from coscience.pm_reasoner import PMCycleOutput
from coscience.substrate import Substrate


def test_edge_ops_roundtrip_through_staging(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    ops = [{"op": "add", "type": "builds_on", "src": "s2", "dst": "s1", "rationale": "uses it"}]
    write_staging(sub, "p1", 0, PMCycleOutput(edge_ops=ops), "fp")
    staged = read_staging(sub, "p1")
    assert staged.output.edge_ops == ops


def test_parse_response_reads_edge_ops():
    text = ('{"report": "r", "edge_ops": ['
            '{"op": "add", "type": "confirms", "src": "s3", "dst": "s2",'
            ' "rationale": "same result", "confidence": "high"}]}')
    out = parse_response(text)
    assert out.edge_ops == [{"op": "add", "type": "confirms", "src": "s3", "dst": "s2",
                             "rationale": "same result", "confidence": "high"}]


def test_parse_response_defaults_edge_ops_empty():
    assert parse_response('{"report": "r"}').edge_ops == []
