package com.offerradar.workflow.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.SQLException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

/**
 * M1's second acceptance check: a duplicate {@code dedup_key} insert must fail. The database, not
 * the application, enforces this because Scout runs nightly and would otherwise race itself.
 */
@Transactional
class DedupKeyUniquenessTest extends PostgresTestBase {

  @Autowired private JdbcTemplate jdbcTemplate;

  @Test
  void secondInsertWithRepeatedDedupKeyFails() {
    long companyId = insertCompany("Acme");
    insertPosting(companyId, "Backend Intern", "https://acme.example/jobs/1", "dk-shared");

    assertThatThrownBy(
            () ->
                insertPosting(
                    companyId, "Frontend Intern", "https://acme.example/jobs/2", "dk-shared"))
        .isInstanceOf(DuplicateKeyException.class)
        .cause()
        .isInstanceOfSatisfying(
            SQLException.class, cause -> assertThat(cause.getSQLState()).isEqualTo("23505"));
  }

  @Test
  void twoDistinctDedupKeysBothInsert() {
    long companyId = insertCompany("Acme");
    insertPosting(companyId, "Backend Intern", "https://acme.example/jobs/1", "dk-one");
    insertPosting(companyId, "Frontend Intern", "https://acme.example/jobs/2", "dk-two");

    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM postings WHERE company_id = ?", Integer.class, companyId);

    assertThat(count).isEqualTo(2);
  }

  private long insertCompany(String name) {
    return jdbcTemplate.queryForObject(
        "INSERT INTO companies (name, normalized_name, source) VALUES (?, ?, 'manual') "
            + "RETURNING id",
        Long.class,
        name,
        name.toLowerCase());
  }

  private void insertPosting(long companyId, String title, String canonicalUrl, String dedupKey) {
    jdbcTemplate.update(
        "INSERT INTO postings (company_id, title, normalized_title, canonical_url, dedup_key) "
            + "VALUES (?, ?, ?, ?, ?)",
        companyId,
        title,
        title.toLowerCase(),
        canonicalUrl,
        dedupKey);
  }
}
