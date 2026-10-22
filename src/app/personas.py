from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    initials: str
    color: str
    voice: str
    description: str

    def public(self) -> dict:
        data = asdict(self)
        data.pop("voice", None)
        return data


# Personas ficticias de demostración (una por pestaña).
PERSONAS = [
    Persona("ana", "Ana García", "Vecina · 38 años", "AG", "#7c3aed", "shimmer",
            "Padrón, ayudas al alquiler y trámites del hogar."),
    Persona("luis", "Luis Romero", "Autónomo · hostelería", "LR", "#0284c7", "ash",
            "Licencias, terrazas, ayudas a autónomos y ZBE."),
    Persona("carmen", "Carmen López", "Jubilada · 74 años", "CL", "#d97706", "sage",
            "Teleasistencia, centros de día y transporte. Prefiere hablar."),
    Persona("diego", "Diego Martín", "Estudiante · 21 años", "DM", "#059669", "verse",
            "Abono joven, becas, bibliotecas y planes culturales."),
    Persona("marta", "Marta Sánchez", "Técnica municipal", "MS", "#dc2626", "coral",
            "Incidencias, datos abiertos y normativa para su trabajo."),
]

PERSONAS_BY_ID = {p.id: p for p in PERSONAS}


def get_persona(user_id: str) -> Persona | None:
    return PERSONAS_BY_ID.get((user_id or "").strip().lower())
