from sqlmodel import SQLModel, MetaData

METADATA = MetaData()


class Base(SQLModel, table=False):
    metadata = METADATA
