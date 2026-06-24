from coscience.resources import ResourcePool, load_pool


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
