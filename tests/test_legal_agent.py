import pytest
from unittest.mock import patch, MagicMock
from agent.legal_agent import ejecutar_agente, ACCIONES_VALIDAS, AGENT_TOOLS

def _make_end_turn_response(text: str):
    mock_resp = MagicMock()
    mock_resp.stop_reason = "end_turn"
    mock_resp.content = [MagicMock(text=text, type="text")]
    mock_resp.usage.input_tokens = 100
    mock_resp.usage.output_tokens = 50
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
        with patch("agent.legal_agent.anthropic.Anthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create.return_value = _make_end_turn_response(
                "## Análisis\nEl caso es laboral por despido injustificado."
            )
            import asyncio
            result = asyncio.run(ejecutar_agente(1, "asesorar", {}, caso))
            assert result["accion"] == "asesorar"
            assert "resultado" in result
            assert len(result["resultado"]) > 0
