package com.offerradar.workflow.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Timestamp;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.postgresql.util.PSQLException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

/**
 * Covers the three schema mechanisms from V001/V003/V004 that no other test touches: the
 * merged/merged_into_id biconditional, the embedding/embedding_model pairing (on both postings and
 * resume_chunks), and the shared set_updated_at() trigger. AGENTS.md §2 rule 6 forbids editing a
 * merged migration, so a logic inversion here is cheap to catch now and a new migration to fix
 * later.
 *
 * <p>One invariant per test method: a failed statement aborts the surrounding transaction, so
 * combining a failing assertion with anything after it in the same method would abort that too.
 */
@Transactional
class SchemaConstraintsTest extends PostgresTestBase {

  // pgvector rejects any length other than 768 for this column; 768 zeros is an arbitrary
  // but valid vector, since these tests only care whether embedding_model is present.
  private static final String VECTOR_768 = vectorLiteral(768);

  @Autowired private JdbcTemplate jdbcTemplate;

  @Test
  void mergedStatusWithNullMergedIntoIdFails() {
    long companyId = insertCompany("Acme");

    assertThatThrownBy(() -> insertPostingWithStatus(companyId, "dk-merged-null", "merged", null))
        .isInstanceOf(DataIntegrityViolationException.class)
        .cause()
        .isInstanceOfSatisfying(
            PSQLException.class,
            cause -> {
              assertThat(cause.getSQLState()).isEqualTo("23514");
              assertThat(cause.getServerErrorMessage().getConstraint())
                  .isEqualTo("ck_postings_merged_pairing");
            });
  }

  @Test
  void nonMergedStatusWithNonNullMergedIntoIdFails() {
    long companyId = insertCompany("Acme");
    long targetId = insertPosting(companyId, "dk-target-1");

    assertThatThrownBy(
            () -> insertPostingWithStatus(companyId, "dk-open-pointing", "open", targetId))
        .isInstanceOf(DataIntegrityViolationException.class)
        .cause()
        .isInstanceOfSatisfying(
            PSQLException.class,
            cause -> {
              assertThat(cause.getSQLState()).isEqualTo("23514");
              assertThat(cause.getServerErrorMessage().getConstraint())
                  .isEqualTo("ck_postings_merged_pairing");
            });
  }

  @Test
  void mergedStatusPointingAtAnotherPostingSucceeds() {
    long companyId = insertCompany("Acme");
    long targetId = insertPosting(companyId, "dk-target-2");

    long mergedId = insertPostingWithStatus(companyId, "dk-merged-valid", "merged", targetId);

    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM postings WHERE id = ? AND status = 'merged' "
                + "AND merged_into_id = ?",
            Integer.class,
            mergedId,
            targetId);
    assertThat(count).isEqualTo(1);
  }

  @Test
  void openStatusWithNullMergedIntoIdSucceeds() {
    long companyId = insertCompany("Acme");

    long postingId = insertPostingWithStatus(companyId, "dk-open-anchor", "open", null);

    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM postings WHERE id = ? AND status = 'open' "
                + "AND merged_into_id IS NULL",
            Integer.class,
            postingId);
    assertThat(count).isEqualTo(1);
  }

  @Test
  void mergedIntoIdPointingAtSelfFailsOnUpdate() {
    // merged_into_id <> id is unreachable by INSERT: id is assigned by the IDENTITY sequence
    // only once the row exists, so no INSERT can name its own id. Only UPDATE can hit this.
    long companyId = insertCompany("Acme");
    long postingId = insertPosting(companyId, "dk-self-merge");

    assertThatThrownBy(
            () ->
                jdbcTemplate.update(
                    "UPDATE postings SET status = 'merged', merged_into_id = id WHERE id = ?",
                    postingId))
        .isInstanceOf(DataIntegrityViolationException.class)
        .cause()
        .isInstanceOfSatisfying(
            PSQLException.class,
            cause -> {
              assertThat(cause.getSQLState()).isEqualTo("23514");
              assertThat(cause.getServerErrorMessage().getConstraint())
                  .isEqualTo("ck_postings_merged_not_self");
            });
  }

  @Test
  void clearingMergedStatusWithMergedIntoIdStillSetFailsOnUpdate() {
    // The shape of a staleness-sweep bug: an UPDATE moves status off 'merged' without also
    // clearing merged_into_id, which ck_postings_merged_pairing exists to catch.
    long companyId = insertCompany("Acme");
    long targetId = insertPosting(companyId, "dk-target-3");
    long mergedId = insertPostingWithStatus(companyId, "dk-merged-sweep", "merged", targetId);

    assertThatThrownBy(
            () ->
                jdbcTemplate.update("UPDATE postings SET status = 'closed' WHERE id = ?", mergedId))
        .isInstanceOf(DataIntegrityViolationException.class)
        .cause()
        .isInstanceOfSatisfying(
            PSQLException.class,
            cause -> {
              assertThat(cause.getSQLState()).isEqualTo("23514");
              assertThat(cause.getServerErrorMessage().getConstraint())
                  .isEqualTo("ck_postings_merged_pairing");
            });
  }

  @Test
  void mergedStatusSetViaUpdateSucceeds() {
    long companyId = insertCompany("Acme");
    long targetId = insertPosting(companyId, "dk-target-4");
    long postingId = insertPosting(companyId, "dk-update-merge");

    jdbcTemplate.update(
        "UPDATE postings SET status = 'merged', merged_into_id = ? WHERE id = ?",
        targetId,
        postingId);

    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM postings WHERE id = ? AND status = 'merged' "
                + "AND merged_into_id = ?",
            Integer.class,
            postingId,
            targetId);
    assertThat(count).isEqualTo(1);
  }

  @Test
  void embeddingWithoutModelFailsForPostings() {
    long companyId = insertCompany("Acme");

    assertThatThrownBy(
            () -> insertPostingWithEmbedding(companyId, "dk-embed-no-model", VECTOR_768, null))
        .isInstanceOf(DataIntegrityViolationException.class)
        .cause()
        .isInstanceOfSatisfying(
            PSQLException.class,
            cause -> {
              assertThat(cause.getSQLState()).isEqualTo("23514");
              assertThat(cause.getServerErrorMessage().getConstraint())
                  .isEqualTo("ck_postings_embedding_model");
            });
  }

  @Test
  void nullEmbeddingAndModelSucceedsForPostings() {
    long companyId = insertCompany("Acme");

    long postingId = insertPostingWithEmbedding(companyId, "dk-embed-null", null, null);

    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM postings WHERE id = ? AND embedding IS NULL "
                + "AND embedding_model IS NULL",
            Integer.class,
            postingId);
    assertThat(count).isEqualTo(1);
  }

  @Test
  void embeddingWithoutModelFailsForResumeChunks() {
    assertThatThrownBy(() -> insertResumeChunk("v-embed-no-model", 0, VECTOR_768, null))
        .isInstanceOf(DataIntegrityViolationException.class)
        .cause()
        .isInstanceOfSatisfying(
            PSQLException.class,
            cause -> {
              assertThat(cause.getSQLState()).isEqualTo("23514");
              assertThat(cause.getServerErrorMessage().getConstraint())
                  .isEqualTo("ck_resume_chunks_embedding_model");
            });
  }

  @Test
  void nullEmbeddingAndModelSucceedsForResumeChunks() {
    insertResumeChunk("v-embed-null", 0, null, null);

    Integer count =
        jdbcTemplate.queryForObject(
            "SELECT count(*) FROM resume_chunks WHERE version = ? AND embedding IS NULL "
                + "AND embedding_model IS NULL",
            Integer.class,
            "v-embed-null");
    assertThat(count).isEqualTo(1);
  }

  @Test
  void updatedAtTriggerFiresOnUpdate() {
    // now() is frozen for the life of the transaction, so a real insert-then-update would see
    // the same timestamp twice; seeding an explicit past value isolates the trigger instead.
    long companyId = insertCompany("Acme");
    Instant past = Instant.now().minusSeconds(3600);
    long postingId =
        jdbcTemplate.queryForObject(
            "INSERT INTO postings (company_id, title, normalized_title, canonical_url, "
                + "dedup_key, updated_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            Long.class,
            companyId,
            "Backend Intern",
            "backend intern",
            "https://acme.example/jobs/dk-trigger",
            "dk-trigger",
            Timestamp.from(past));

    jdbcTemplate.update("UPDATE postings SET title = ? WHERE id = ?", "Updated Title", postingId);

    Boolean updatedToNow =
        jdbcTemplate.queryForObject(
            "SELECT updated_at = now() FROM postings WHERE id = ?", Boolean.class, postingId);

    assertThat(updatedToNow).isTrue();
  }

  private long insertCompany(String name) {
    return jdbcTemplate.queryForObject(
        "INSERT INTO companies (name, normalized_name, source) VALUES (?, ?, 'manual') "
            + "RETURNING id",
        Long.class,
        name,
        name.toLowerCase());
  }

  private long insertPosting(long companyId, String dedupKey) {
    return jdbcTemplate.queryForObject(
        "INSERT INTO postings (company_id, title, normalized_title, canonical_url, dedup_key) "
            + "VALUES (?, ?, ?, ?, ?) RETURNING id",
        Long.class,
        companyId,
        "Backend Intern",
        "backend intern",
        "https://acme.example/jobs/" + dedupKey,
        dedupKey);
  }

  private long insertPostingWithStatus(
      long companyId, String dedupKey, String status, Long mergedIntoId) {
    return jdbcTemplate.queryForObject(
        "INSERT INTO postings (company_id, title, normalized_title, canonical_url, dedup_key, "
            + "status, merged_into_id) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        Long.class,
        companyId,
        "Backend Intern",
        "backend intern",
        "https://acme.example/jobs/" + dedupKey,
        dedupKey,
        status,
        mergedIntoId);
  }

  private long insertPostingWithEmbedding(
      long companyId, String dedupKey, String embeddingLiteral, String embeddingModel) {
    return jdbcTemplate.queryForObject(
        "INSERT INTO postings (company_id, title, normalized_title, canonical_url, dedup_key, "
            + "embedding, embedding_model) VALUES (?, ?, ?, ?, ?, ?::vector, ?) RETURNING id",
        Long.class,
        companyId,
        "Backend Intern",
        "backend intern",
        "https://acme.example/jobs/" + dedupKey,
        dedupKey,
        embeddingLiteral,
        embeddingModel);
  }

  private long insertResumeChunk(
      String version, int ordinal, String embeddingLiteral, String embeddingModel) {
    return jdbcTemplate.queryForObject(
        "INSERT INTO resume_chunks (version, kind, ordinal, text, embedding, embedding_model) "
            + "VALUES (?, 'bullet', ?, ?, ?::vector, ?) RETURNING id",
        Long.class,
        version,
        ordinal,
        "Built a thing.",
        embeddingLiteral,
        embeddingModel);
  }

  private static String vectorLiteral(int dimensions) {
    StringBuilder literal = new StringBuilder("[");
    for (int i = 0; i < dimensions; i++) {
      if (i > 0) {
        literal.append(',');
      }
      literal.append('0');
    }
    return literal.append(']').toString();
  }
}
