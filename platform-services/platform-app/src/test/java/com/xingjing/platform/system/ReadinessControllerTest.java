package com.xingjing.platform.system;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.autoconfigure.exclude="
                + "org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration,"
                + "org.springframework.boot.flyway.autoconfigure.FlywayAutoConfiguration,"
                + "org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,"
                + "org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration"
})
class ReadinessControllerTest {
    @Autowired
    MockMvc mvc;

    @Test
    void exposesVersionedReadinessEndpoint() throws Exception {
        mvc.perform(get("/api/v1/system/readiness"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.service").value("xingjing-platform"))
                .andExpect(jsonPath("$.status").value("UP"));
    }
}
