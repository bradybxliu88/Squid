from tests.harness.core.backend_context import BackendContext


def test_display_info_returns_color_and_wavelength():
    with BackendContext() as ctx:
        ccm = ctx.microscope.channel_configuration_manager
        objective = ctx.microscope.objective_store.current_objective
        info = ccm.get_channel_display_info(objective)
        assert len(info) > 0
        for item in info:
            assert item.name
            assert item.color.startswith("#")
            # ex_wavelength is Optional[int]
            assert item.ex_wavelength is None or isinstance(item.ex_wavelength, int)
