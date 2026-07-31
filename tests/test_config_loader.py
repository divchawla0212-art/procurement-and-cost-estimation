from cost_estimation.config.loader import load_config, CANONICAL_ROLES, DisciplineConfig


def test_loads_packaged_default():
    cfg = load_config()
    assert isinstance(cfg, DisciplineConfig)
    assert cfg.disciplines["3D"] == "electrical"
    assert "TF" in cfg.areas
    assert cfg.manhour_rate_default == 70.0


def test_canonical_roles_match_config_keys():
    cfg = load_config()
    assert set(CANONICAL_ROLES) == set(cfg.column_roles.keys())
