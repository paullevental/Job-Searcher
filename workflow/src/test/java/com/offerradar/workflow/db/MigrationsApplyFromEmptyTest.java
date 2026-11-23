package com.offerradar.workflow.db;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * M1's first acceptance check: migrations apply from empty in CI. The container runs the pgvector
 * image because the schema declares a {@code vector} column.
 */
class MigrationsApplyFromEmptyTest extends PostgresTestBase {

  private static final List<String> EXPECTED_TABLES =
      List.of(
          "companies",
          "ats_boards",
          "postings",
          "posting_sightings",
          "resume_chunks",
          "matches",
          "match_analyses",
          "contacts",
          "outreach_drafts",
          "agent_runs",
          "approvals",
          "llm_calls",
          "source_cursors");

  @Autowired private JdbcTemplate jdbcTemplate;

  @Test
  void allExpectedTablesExist() {
    List<String> tables =
        jdbcTemplate.queryForList(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
            String.class);

    assertThat(tables).contains(EXPECTED_TABLES.toArray(new String[0]));
  }

  @Test
  void everyMigrationSucceeded() {
    Integer failedCount =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM flyway_schema_history WHERE success = false", Integer.class);
    Integer appliedCount =
        jdbcTemplate.queryForObject("SELECT count(*) FROM flyway_schema_history", Integer.class);

    assertThat(failedCount).isZero();
    // 11 = V001..V011; bump deliberately when a migration is added, not as a side effect.
    assertThat(appliedCount).isEqualTo(11);
  }

  @Test
  void vectorExtensionIsInstalled() {
    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM pg_extension WHERE extname = 'vector'", Integer.class);

    assertThat(count).isEqualTo(1);
  }
}
