# Java Lineage Skill

## Java-Specific Data Access Patterns

### JDBC / JdbcTemplate
- `jdbcTemplate.query("SELECT ...", mapper)` — READ from table in SQL
- `jdbcTemplate.update("INSERT INTO ...", params)` — WRITE to table
- `jdbcTemplate.execute("CALL proc_name(...)")` — Stored procedure call
- `connection.prepareStatement("SQL")` — Parse the SQL string for table names
- `CallableStatement` — Stored procedure: note proc name as data operation

### JPA / Hibernate
- `entityManager.find(Entity.class, id)` — READ by primary key
- `entityManager.persist(entity)` — WRITE (INSERT)
- `entityManager.merge(entity)` — WRITE (UPDATE)
- `entityManager.createQuery("JPQL")` — Parse JPQL for entity names
- `entityManager.createNativeQuery("SQL")` — Raw SQL
- `@Entity` class → maps to a table (use table name or entity name)

### Spring Data Repositories
- `repository.findById(id)` — READ
- `repository.findAll()` — READ all
- `repository.save(entity)` — WRITE
- `repository.deleteById(id)` — DELETE
- `@Query("SELECT ...")` — Custom query, parse SQL/JPQL
- Method name queries: `findByNameAndAge(...)` → READ with conditions

### MyBatis
- `@Select("SQL")` — READ
- `@Insert("SQL")` — WRITE
- `@Update("SQL")` — UPDATE
- `@Delete("SQL")` — DELETE
- `sqlSession.selectList("mapper.method")` — READ

### REST Endpoints (Spring)
- `@GetMapping("/path")` — Serves data (trace source)
- `@PostMapping("/path")` — Receives data (trace target)
- `@RequestBody` parameter — Incoming data
- `ResponseEntity<T>` return — Outgoing data

### REST Clients
- `restTemplate.getForObject(url, Type.class)` — READ from external API
- `restTemplate.postForObject(url, body, Type.class)` — WRITE to external API
- `WebClient.get().uri(url).retrieve()` — READ
- `WebClient.post().uri(url).body(data)` — WRITE

### Kafka
- `kafkaTemplate.send(topic, message)` — WRITE to Kafka topic
- `@KafkaListener(topics = "topic")` — READ from Kafka topic

### Stored Procedures
- `SimpleJdbcCall.execute(params)` — SP execution
- `@Procedure("proc_name")` — SP via Spring
- `CallableStatement.execute()` — SP via JDBC
- Trace: input params as source columns, output/affected table as target

### Evidence Chain Example (Java)
```
Step 1: [UserDAO.java:25] List<User> users = jdbcTemplate.query("SELECT id, name FROM users", rowMapper)
        → "Read user records from users table via JDBC"
Step 2: [UserService.java:40] List<UserDTO> dtos = users.stream().map(u -> new UserDTO(u.getId(), u.getName().toUpperCase())).collect(toList())
        → "Transform: map User to UserDTO, uppercase name"
Step 3: [UserService.java:45] List<UserDTO> filtered = dtos.stream().filter(UserDTO::isActive).collect(toList())
        → "Filter: only active users"
Step 4: [UserController.java:20] return ResponseEntity.ok(filtered)
        → "Write: return filtered users via GET /api/users endpoint"
```
