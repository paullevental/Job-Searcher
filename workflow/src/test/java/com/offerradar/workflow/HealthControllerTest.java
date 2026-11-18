package com.offerradar.workflow;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthEndpoint;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Web slice only: no datasource, no Redis, no containers, so this runs offline (AGENTS.md §2 rule
 * 5). Real dependency wiring gets a Testcontainers test in M1 when there is a schema to verify.
 */
@WebMvcTest(HealthController.class)
class HealthControllerTest {

  @Autowired private MockMvc mvc;

  @MockitoBean private HealthEndpoint healthEndpoint;

  @Test
  void healthAnswersWithoutConsultingDependencies() throws Exception {
    mvc.perform(get("/health"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.status").value("ok"))
        .andExpect(jsonPath("$.service").value("workflow"));
  }

  @Test
  void readyIsOkWhenActuatorReportsUp() throws Exception {
    when(healthEndpoint.health()).thenReturn(Health.up().build());

    mvc.perform(get("/ready"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.status").value("ready"));
  }

  @Test
  void readyIs503WhenActuatorReportsDown() throws Exception {
    when(healthEndpoint.health()).thenReturn(Health.down().build());

    mvc.perform(get("/ready"))
        .andExpect(status().isServiceUnavailable())
        .andExpect(jsonPath("$.status").value("not ready"));
  }
}
