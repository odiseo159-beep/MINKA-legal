"""
seed_demo.py — Inserta datos de prueba para demostración.
Ejecutar: python -m scripts.seed_demo
Para limpiar: python -m scripts.seed_demo --clean
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.cases_db import init_cases_db, crear_caso, listar_casos, eliminar_caso

DEMO_CASES = [
    {
        "telefono": "912345678",
        "nombre_cliente": "María García López",
        "expediente": "EXP-2026-0142",
        "tipo_caso": "Alimentos",
        "estado": "en_tramite",
        "proxima_fecha": "2026-04-10",
        "proxima_accion": "Audiencia de conciliación",
        "documentos_pendientes": "Boletas de pago últimos 3 meses",
        "notas": "Cliente solicita pensión de alimentos para 2 hijos menores. Demandado trabaja como independiente.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "923456789",
        "nombre_cliente": "Carlos Mendoza Ríos",
        "expediente": "EXP-2026-0087",
        "tipo_caso": "Penal - Estafa",
        "estado": "en_audiencia",
        "proxima_fecha": "2026-04-08",
        "proxima_accion": "Juicio oral - segunda sesión",
        "documentos_pendientes": "Peritaje contable, testimoniales de testigos 3 y 4",
        "notas": "Caso de estafa por S/ 45,000. Acusado tiene antecedentes. Víctima presentó pruebas bancarias.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "934567890",
        "nombre_cliente": "Ana Lucía Vargas",
        "expediente": "EXP-2026-0201",
        "tipo_caso": "Laboral",
        "estado": "nuevo",
        "proxima_fecha": "2026-04-15",
        "proxima_accion": "Presentar demanda ante juzgado laboral",
        "documentos_pendientes": "Contrato de trabajo, boletas de pago, liquidación de beneficios",
        "notas": "Despido arbitrario de empresa minera. Trabajó 5 años. Solicita reposición + indemnización.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "945678901",
        "nombre_cliente": "Roberto Huamán Torres",
        "expediente": "EXP-2025-0892",
        "tipo_caso": "Civil - Desalojo",
        "estado": "pendiente_documento",
        "proxima_fecha": "2026-04-12",
        "proxima_accion": "Presentar escritura pública notarizada",
        "documentos_pendientes": "Escritura pública del inmueble, contrato de arrendamiento vencido, fotos del inmueble",
        "notas": "Inquilino no paga hace 8 meses. Propiedad en Miraflores. Se intentó conciliación sin éxito.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "956789012",
        "nombre_cliente": "Patricia Sánchez Medina",
        "expediente": "EXP-2026-0156",
        "tipo_caso": "Alimentos",
        "estado": "resuelto",
        "proxima_fecha": "",
        "proxima_accion": "",
        "documentos_pendientes": "",
        "notas": "Caso resuelto favorablemente. Pensión fijada en 30% de ingresos del demandado. Sentencia firme.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "967890123",
        "nombre_cliente": "Jorge Castillo Flores",
        "expediente": "EXP-2026-0178",
        "tipo_caso": "Penal - Estafa",
        "estado": "en_revision",
        "proxima_fecha": "2026-04-20",
        "proxima_accion": "Revisión de apelación por sala penal",
        "documentos_pendientes": "Escrito de apelación fundamentado",
        "notas": "Sentencia de primera instancia desfavorable. Apelamos por errores en valoración de pruebas.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "978901234",
        "nombre_cliente": "Luciana Paredes Gutiérrez",
        "expediente": "EXP-2026-0210",
        "tipo_caso": "Laboral",
        "estado": "en_tramite",
        "proxima_fecha": "2026-04-18",
        "proxima_accion": "Audiencia de juzgamiento",
        "documentos_pendientes": "Certificado de trabajo, correos de hostigamiento laboral",
        "notas": "Demanda por hostigamiento laboral y discriminación de género. Empresa de telecomunicaciones.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "912345678",
        "nombre_cliente": "María García López",
        "expediente": "EXP-2025-0650",
        "tipo_caso": "Civil - Desalojo",
        "estado": "archivado",
        "proxima_fecha": "",
        "proxima_accion": "",
        "documentos_pendientes": "",
        "notas": "Caso anterior de la misma clienta. Desalojo resuelto por conciliación. Archivado.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "989012345",
        "nombre_cliente": "Fernando Quispe Mamani",
        "expediente": "EXP-2026-0225",
        "tipo_caso": "Penal - Estafa",
        "estado": "nuevo",
        "proxima_fecha": "2026-04-22",
        "proxima_accion": "Denuncia ante fiscalía",
        "documentos_pendientes": "Contrato falsificado, transferencias bancarias, declaración jurada",
        "notas": "Estafa piramidal. Víctima perdió S/ 120,000. Hay otros 15 afectados que podrían sumarse.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "990123456",
        "nombre_cliente": "Sofía Delgado Rojas",
        "expediente": "EXP-2026-0198",
        "tipo_caso": "Alimentos",
        "estado": "en_apelacion",
        "proxima_fecha": "2026-04-25",
        "proxima_accion": "Vista de causa en sala superior",
        "documentos_pendientes": "Informe social actualizado",
        "notas": "Apelación de sentencia que fijó pensión baja (15%). Solicitamos incremento a 30%.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "923456789",
        "nombre_cliente": "Carlos Mendoza Ríos",
        "expediente": "EXP-2026-0240",
        "tipo_caso": "Laboral",
        "estado": "en_tramite",
        "proxima_fecha": "2026-04-28",
        "proxima_accion": "Conciliación administrativa en SUNAFIL",
        "documentos_pendientes": "Cálculo de beneficios sociales",
        "notas": "Segundo caso del mismo cliente. Reclamo de CTS y gratificaciones no pagadas por 2 años.",
        "abogado_asignado": "Daniel",
    },
    {
        "telefono": "901234567",
        "nombre_cliente": "Diego Ramírez Vega",
        "expediente": "EXP-2026-0255",
        "tipo_caso": "Civil - Desalojo",
        "estado": "en_tramite",
        "proxima_fecha": "2026-04-06",
        "proxima_accion": "Inspección judicial del inmueble",
        "documentos_pendientes": "",
        "notas": "Desalojo por ocupación precaria. Inmueble comercial en el Centro de Lima. Urgente.",
        "abogado_asignado": "Daniel",
    },
]


def seed():
    init_cases_db()
    existing = listar_casos()
    if len(existing) > 0:
        print(f"Ya existen {len(existing)} casos en la base de datos.")
        resp = input("¿Deseas agregar los datos demo de todas formas? (s/n): ").strip().lower()
        if resp != "s":
            print("Cancelado.")
            return

    print(f"Insertando {len(DEMO_CASES)} casos de prueba...")
    for caso in DEMO_CASES:
        created = crear_caso(caso)
        print(f"  ✓ {created['nombre_cliente']} — {created['tipo_caso']} ({created['estado']})")

    print(f"\n✅ {len(DEMO_CASES)} casos demo insertados exitosamente.")
    print("Abre el dashboard para ver los datos: https://minka-front.vercel.app/dashboard")


def clean():
    init_cases_db()
    casos = listar_casos()
    demo_names = {c["nombre_cliente"] for c in DEMO_CASES}
    deleted = 0
    for caso in casos:
        if caso["nombre_cliente"] in demo_names:
            eliminar_caso(caso["id"])
            deleted += 1
    print(f"🗑️ {deleted} casos demo eliminados.")


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    else:
        seed()
