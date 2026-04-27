import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from agent.legal_agent import ejecutar_agente, ACCIONES_VALIDAS, AGENT_TOOLS

def _make_end_turn_response(text: str):
    mock_resp = MagicMock()
    mock_resp.stop_reason = "end_turn"
    mock_resp.content = [MagicMock(text=text, type="text")]
    mock_resp.usage.input_tokens = 100
    mock_resp.usage.output_tokens = 50
    mock_resp.usage.cache_read_input_tokens = 0
    return mock_resp

def _make_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tu_1"):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_id
    mock_resp = MagicMock()
    mock_resp.stop_reason = "tool_use"
    mock_resp.content = [block]
    mock_resp.usage.input_tokens = 50
    mock_resp.usage.output_tokens = 20
    mock_resp.usage.cache_read_input_tokens = 0
    return mock_resp

def _make_caso():
    return {
        "id": 1, "nombre_cliente": "Ana Torres", "tipo_caso": "laboral",
        "estado": "en_tramite", "expediente": "12345-2026",
        "notas": "Despido injustificado", "telefono": "987654321",
    }

class TestAgenteValido:
    def test_acciones_validas_definidas(self):
        assert "analizar" in ACCIONES_VALIDAS
        assert "asesorar" in ACCIONES_VALIDAS
        assert "redactar" in ACCIONES_VALIDAS
        assert "normativa" in ACCIONES_VALIDAS

    def test_tools_definidas(self):
        nombres = [t["name"] for t in AGENT_TOOLS]
        assert "get_case_documents" in nombres
        assert "search_normativa" in nombres
        assert "consejo_procesal" in nombres

    def test_accion_invalida_lanza_error(self):
        with pytest.raises(ValueError, match="Acción no válida"):
            import asyncio
            asyncio.run(ejecutar_agente(1, "inventada", {}, _make_caso()))

class TestEjecucionAgente:
    def test_respuesta_exitosa(self):
        caso = _make_caso()
        with patch("agent.legal_agent.anthropic.AsyncAnthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(return_value=_make_end_turn_response(
                "## Análisis\nEl caso es laboral por despido injustificado."
            ))
            import asyncio
            result = asyncio.run(ejecutar_agente(1, "asesorar", {}, caso))
            assert result["accion"] == "asesorar"
            assert "resultado" in result
            assert len(result["resultado"]) > 0

    def test_tool_use_then_end_turn(self):
        caso = _make_caso()
        with patch("agent.legal_agent.anthropic.AsyncAnthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=[
                _make_tool_use_response("consejo_procesal", {}),
                _make_end_turn_response("## Asesoría\nEl caso tiene buena posición."),
            ])
            with patch("agent.legal_agent._consejo_procesal", return_value="Etapa: investigación"):
                import asyncio
                result = asyncio.run(ejecutar_agente(1, "asesorar", {}, caso))
                assert result["accion"] == "asesorar"
                assert "Asesoría" in result["resultado"]
                assert "consejo_procesal" in result["tools_usados"]

    def test_max_iterations_raises(self):
        caso = _make_caso()
        with patch("agent.legal_agent.anthropic.AsyncAnthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(
                return_value=_make_tool_use_response("consejo_procesal", {})
            )
            with patch("agent.legal_agent._consejo_procesal", return_value="consejo"):
                with pytest.raises(RuntimeError, match="convergió"):
                    import asyncio
                    asyncio.run(ejecutar_agente(1, "asesorar", {}, caso))
