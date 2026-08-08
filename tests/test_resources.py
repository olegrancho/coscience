from coscience.resources import ResourcePool, load_pool
from coscience.resources import WORKER_KEY, effective_requirement


def test_from_dict_bare_mapping_coerces_floats():
    pool = ResourcePool.from_dict({"gpu_24gb": 1, "cpu": 32})
    assert pool.capacity == {"gpu_24gb": 1.0, "cpu": 32.0}


def test_from_dict_accepts_resources_wrapper():
    pool = ResourcePool.from_dict({"resources": {"gpu_24gb": 1}})
    assert pool.capacity == {"gpu_24gb": 1.0}


def test_from_yaml_roundtrip(tmp_path):
    p = tmp_path / "resources.yaml"
    p.write_text("resources:\n  gpu_24gb: 1\n  disk_gb: 500\n")
    pool = ResourcePool.from_yaml(p)
    assert pool.capacity == {"gpu_24gb": 1.0, "disk_gb": 500.0}


def test_load_pool_missing_returns_empty(tmp_path):
    assert load_pool(tmp_path).capacity == {}


def test_load_pool_reads_coscience_dir(tmp_path):
    d = tmp_path / ".coscience"
    d.mkdir()
    (d / "resources.yaml").write_text("resources:\n  runtime_slots: 4\n")
    assert load_pool(tmp_path).capacity == {"runtime_slots": 4.0}


def test_effective_requirement_is_unchanged_without_a_worker_cap():
    pool = ResourcePool({"cpu": 8.0})
    assert effective_requirement({"cpu": 2.0}, pool) == {"cpu": 2.0}


def test_effective_requirement_adds_a_worker_slot_when_capped():
    pool = ResourcePool({"cpu": 8.0, WORKER_KEY: 2.0})
    assert effective_requirement({"cpu": 2.0}, pool) == {"cpu": 2.0, WORKER_KEY: 1.0}


def test_effective_requirement_bounds_a_sprint_declaring_nothing():
    pool = ResourcePool({WORKER_KEY: 1.0})
    assert effective_requirement({}, pool) == {WORKER_KEY: 1.0}


def test_effective_requirement_does_not_mutate_its_input():
    pool = ResourcePool({WORKER_KEY: 1.0})
    required = {"cpu": 1.0}
    effective_requirement(required, pool)
    assert required == {"cpu": 1.0}


def test_effective_requirement_ignores_a_self_declared_worker_amount():
    # A sprint may not buy itself extra slots; one agent is one slot.
    pool = ResourcePool({WORKER_KEY: 4.0})
    assert effective_requirement({WORKER_KEY: 3.0}, pool) == {WORKER_KEY: 1.0}
