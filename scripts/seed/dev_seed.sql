-- Dev seed: a handful of real companies with one ATS board each
-- (AGENTS.md §11 M1). board_token is each company's lowercase slug, not a
-- verified token -- M2's discover_board confirms or corrects these against
-- the real provider API.

INSERT INTO companies (name, normalized_name, domain, source)
VALUES
    ('Stripe', 'stripe', 'stripe.com', 'manual'),
    ('Figma', 'figma', 'figma.com', 'manual'),
    ('Airbnb', 'airbnb', 'airbnb.com', 'manual'),
    ('Notion', 'notion', 'notion.so', 'manual'),
    ('Robinhood', 'robinhood', 'robinhood.com', 'manual'),
    ('Brex', 'brex', 'brex.com', 'manual'),
    ('Discord', 'discord', 'discord.com', 'manual'),
    ('Asana', 'asana', 'asana.com', 'manual')
ON CONFLICT (normalized_name) DO NOTHING;

INSERT INTO ats_boards (company_id, provider, board_token)
SELECT c.id, v.provider, v.board_token
FROM (VALUES
    ('stripe', 'greenhouse', 'stripe'),
    ('figma', 'greenhouse', 'figma'),
    ('airbnb', 'greenhouse', 'airbnb'),
    ('notion', 'lever', 'notion'),
    ('robinhood', 'lever', 'robinhood'),
    ('brex', 'ashby', 'brex'),
    ('discord', 'ashby', 'discord'),
    ('asana', 'workable', 'asana')
) AS v (normalized_name, provider, board_token)
JOIN companies c ON c.normalized_name = v.normalized_name
ON CONFLICT (company_id, provider, board_token) DO NOTHING;
