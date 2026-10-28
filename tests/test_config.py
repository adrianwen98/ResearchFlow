from web_agent.config import Settings


def test_load_default_paths(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    s = Settings.load()
    assert s.api_key == "test-only"
    assert s.output_dir.name == "outputs"
    assert s.max_graph_steps <= 100
