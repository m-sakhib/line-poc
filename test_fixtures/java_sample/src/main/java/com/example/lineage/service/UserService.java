package com.example.lineage.service;

import com.example.lineage.dao.UserDAO;
import com.example.lineage.dto.UserDTO;
import com.example.lineage.model.User;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.stream.Collectors;

@Service
public class UserService {

    private final UserDAO userDAO;

    public UserService(UserDAO userDAO) {
        this.userDAO = userDAO;
    }

    public List<UserDTO> getActiveUsers() {
        List<User> users = userDAO.findAllUsers();
        return users.stream()
            .filter(User::isActive)
            .map(u -> new UserDTO(
                u.getId(),
                u.getName().toUpperCase(),
                u.getEmail()
            ))
            .collect(Collectors.toList());
    }

    public UserDTO getUserById(Long id) {
        User user = userDAO.findUserById(id);
        return new UserDTO(user.getId(), user.getName(), user.getEmail());
    }

    public UserDTO createUser(UserDTO dto) {
        User user = new User();
        user.setName(dto.getName());
        user.setEmail(dto.getEmail());
        user.setActive(true);
        userDAO.insertUser(user);
        return new UserDTO(user.getId(), user.getName(), user.getEmail());
    }
}
