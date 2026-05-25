UPSERT_TABLE = """
MERGE (t:Table { id: $id })
SET t.source_id = $source_id,
    t.database = $database,
    t.name = $name,
    t.title = $title,
    t.domain = $domain,
    t.status = $status
WITH t
OPTIONAL MATCH (t)-[old:BELONGS_TO_DOMAIN]->()
DELETE old
WITH t
WHERE $domain IS NOT NULL
MERGE (d:Domain { name: $domain })
MERGE (t)-[:BELONGS_TO_DOMAIN]->(d)
"""

UPSERT_COLUMN = """
MERGE (t:Table { id: $table_id })
MERGE (c:Column { id: $id })
SET c.table_id = $table_id,
    c.name = $name,
    c.data_type = $data_type,
    c.role = $role,
    c.status = $status
MERGE (t)-[:HAS_COLUMN]->(c)
"""

UPSERT_RELATION = """
MATCH (a:Column { id: $from_col })
MATCH (b:Column { id: $to_col })
MERGE (a)-[r:REFERENCES_INFERRED]->(b)
SET r.kind = $kind, r.confidence = $confidence, r.reasoning = $reasoning
"""

NEIGHBORS = """
MATCH (t:Table { id: $table_id })-[:HAS_COLUMN]->(:Column)
  -[:REFERENCES|REFERENCES_INFERRED*1..2]-(:Column)
  <-[:HAS_COLUMN]-(other:Table)
WHERE other.id <> t.id
RETURN DISTINCT other.id AS id, other.name AS name, other.database AS database
LIMIT 50
"""

SUBGRAPH_FOR_SOURCE = """
MATCH (t:Table { source_id: $source_id })
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
OPTIONAL MATCH (c)-[r:REFERENCES|REFERENCES_INFERRED]-(c2:Column)<-[:HAS_COLUMN]-(t2:Table)
RETURN t.id AS t_id, t.name AS t_name, t.database AS t_db, t.title AS t_title,
       collect(DISTINCT { id: c.id, name: c.name, type: c.data_type, role: c.role }) AS columns,
       collect(DISTINCT { from: c.id, to: c2.id, kind: type(r), confidence: r.confidence }) AS edges
"""
