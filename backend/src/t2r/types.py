from typing import NewType
from uuid import UUID

SourceId = NewType("SourceId", UUID)
RunId = NewType("RunId", UUID)
TaskId = NewType("TaskId", UUID)
SessionId = NewType("SessionId", UUID)
TableId = NewType("TableId", UUID)
ColumnId = NewType("ColumnId", UUID)
NoteId = NewType("NoteId", UUID)
