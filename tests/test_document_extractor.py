import pytest
from unittest.mock import patch, MagicMock
from agent.document_extractor import extraer_datos_documento


def _mock_anthropic_response(text: str):
    """Helper para mockear respuesta de Claude."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=text)]
    return mock_msg


class TestImageSupport:
    @pytest.fixture(autouse=True)
    def set_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def test_rechaza_formato_no_soportado(self):
        with pytest.raises(ValueError, match="Formato no soportado"):
            extraer_datos_documento(b"data", "doc.bmp", "image/bmp")

    def test_acepta_jpg(self):
        with patch("agent.document_extractor.anthropic.Anthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create.return_value = _mock_anthropic_response(
                '{"legible": true, "es_legal": true, "nombre_cliente": "Test", "telefono": "987654321"}'
            )
            result = extraer_datos_documento(b"fake_jpg", "foto.jpg", "image/jpeg")
            assert result["campos"]["nombre_cliente"] == "Test"
            assert result.get("legible") is not None

    def test_acepta_png(self):
        with patch("agent.document_extractor.anthropic.Anthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create.return_value = _mock_anthropic_response(
                '{"legible": true, "es_legal": true, "nombre_cliente": "Ana"}'
            )
            result = extraer_datos_documento(b"fake_png", "resolucion.png", "image/png")
            assert result["campos"]["nombre_cliente"] == "Ana"

    def test_acepta_webp(self):
        with patch("agent.document_extractor.anthropic.Anthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create.return_value = _mock_anthropic_response(
                '{"legible": true, "es_legal": true}'
            )
            result = extraer_datos_documento(b"fake_webp", "doc.webp", "image/webp")
            assert result.get("legible") is True

    def test_rechazo_por_ilegible(self):
        with patch("agent.document_extractor.anthropic.Anthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create.return_value = _mock_anthropic_response(
                '{"legible": false, "es_legal": false, "rejection_reason": "Imagen borrosa"}'
            )
            result = extraer_datos_documento(b"blurry", "borrosa.jpg", "image/jpeg")
            assert result["legible"] is False
            assert result["rejection_reason"] == "Imagen borrosa"
            assert result["campos"] == {}

    def test_respuesta_incluye_campos_validacion_en_pdf(self):
        """PDF responses should also include validation fields (defaulting to True)"""
        with patch("agent.document_extractor.anthropic.Anthropic") as MockClient:
            mock_client = MockClient.return_value
            mock_client.messages.create.return_value = _mock_anthropic_response(
                '{"nombre_cliente": "Pedro", "telefono": "912345678"}'
            )
            result = extraer_datos_documento(b"%PDF-fake", "doc.pdf", "application/pdf")
            assert "legible" in result
            assert "es_legal" in result
            assert "rejection_reason" in result
            assert result["legible"] is True
