package com.example.lineage.dao;

import com.example.lineage.model.User;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;

@Repository
public class UserDAO {

    private final JdbcTemplate jdbcTemplate;

    public UserDAO(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<User> findAllUsers() {
        return jdbcTemplate.query(
            "SELECT id, name, email, active FROM users",
            new UserRowMapper()
        );
    }

    public User findUserById(Long id) {
        return jdbcTemplate.queryForObject(
            "SELECT id, name, email, active FROM users WHERE id = ?",
            new UserRowMapper(),
            id
        );
    }

    public void insertUser(User user) {
        jdbcTemplate.update(
            "INSERT INTO users (name, email, active) VALUES (?, ?, ?)",
            user.getName(),
            user.getEmail(),
            user.isActive()
        );
    }

    public void callAuditProcedure(Long userId, String action) {
        jdbcTemplate.update("CALL sp_audit_user_action(?, ?)", userId, action);
    }

    private static class UserRowMapper implements RowMapper<User> {
        @Override
        public User mapRow(ResultSet rs, int rowNum) throws SQLException {
            User user = new User();
            user.setId(rs.getLong("id"));
            user.setName(rs.getString("name"));
            user.setEmail(rs.getString("email"));
            user.setActive(rs.getBoolean("active"));
            return user;
        }
    }
}
