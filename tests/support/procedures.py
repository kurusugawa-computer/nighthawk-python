from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureDescriptor


def square(value: int) -> int:
    """Return the integer's square."""
    return value * value


def make_catalog() -> ProcedureCatalog:
    catalog = ProcedureCatalog()
    catalog.register(
        ProcedureDescriptor(procedure_id="square", name="square", purpose="Square an integer", source_provenance_class="ratified"), square
    )
    catalog.register(
        ProcedureDescriptor(procedure_id="identity", name="identity", purpose="Return an integer unchanged", source_provenance_class="ratified"),
        lambda value: value,
    )
    return catalog
