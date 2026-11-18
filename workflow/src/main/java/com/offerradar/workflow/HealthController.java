package com.offerradar.workflow;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.actuate.health.CompositeHealth;
import org.springframework.boot.actuate.health.HealthComponent;
import org.springframework.boot.actuate.health.HealthEndpoint;
import org.springframework.boot.actuate.health.Status;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Liveness and readiness, matching the agents service so Compose can treat both the same way.
 *
 * <p>/health answers from the process alone. /ready delegates to Actuator, whose db and redis
 * indicators do the real dependency checks.
 */
@RestController
class HealthController {

  private static final String SERVICE = "workflow";
  private final HealthEndpoint healthEndpoint;

  HealthController(HealthEndpoint healthEndpoint) {
    this.healthEndpoint = healthEndpoint;
  }

  @GetMapping("/health")
  Map<String, String> health() {
    return Map.of("status", "ok", "service", SERVICE);
  }

  @GetMapping("/ready")
  ResponseEntity<Map<String, Object>> ready() {
    HealthComponent health = healthEndpoint.health();
    boolean up = Status.UP.equals(health.getStatus());

    Map<String, String> checks = new LinkedHashMap<>();
    if (health instanceof CompositeHealth composite) {
      composite
          .getComponents()
          .forEach((name, component) -> checks.put(name, component.getStatus().getCode()));
    }

    Map<String, Object> body = new LinkedHashMap<>();
    body.put("status", up ? "ready" : "not ready");
    body.put("service", SERVICE);
    body.put("checks", checks);

    return ResponseEntity.status(up ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE).body(body);
  }
}
