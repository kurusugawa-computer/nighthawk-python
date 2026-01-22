def test_openai_client_import_and_agent_construction_and_run():
    from nighthawk.configuration import Configuration
    from nighthawk.openai_client import make_agent

    cfg = Configuration(
        model="openai:gpt-5-nano",
    )
    agent = make_agent(cfg)

    result = agent.run_sync(
        'Return exactly this JSON object and nothing else: {"effect": {"type": "continue", "value_json": null}, "error": null}',
    )

    assert result.output.effect is not None
    assert result.output.effect.type in ("continue", "break", "return")
    assert result.output.error is None
