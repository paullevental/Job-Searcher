package com.offerradar.workflow.db;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * Shared Testcontainers Postgres for M1's schema tests, started once for every class that extends
 * this base.
 *
 * <p>The container is a context bean rather than a {@code @Container} field on purpose. JUnit's
 * {@code @Testcontainers} extension stops a static container when its own test class finishes,
 * while Spring caches a single context across all the classes sharing this configuration — so the
 * second class to run inherits a DataSource still pointing at the first class's stopped container
 * and every test fails to connect. Declared as a {@code Startable} bean, the container's lifetime
 * matches the cached context instead.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@Import(PostgresTestBase.Containers.class)
abstract class PostgresTestBase {

  @TestConfiguration(proxyBeanMethods = false)
  static class Containers {

    // V001 runs CREATE EXTENSION vector; the canonical postgres image has no such extension.
    // asCompatibleSubstituteFor lets Testcontainers accept this non-canonical image as a postgres.
    @Bean
    @ServiceConnection
    PostgreSQLContainer<?> postgres() {
      return new PostgreSQLContainer<>(
          DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"));
    }
  }
}
