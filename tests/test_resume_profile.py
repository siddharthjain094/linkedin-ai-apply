from agent.resume.profile import extract_contact, extract_profile_fields

RESUME = """\
Jane Q. Developer
San Francisco, CA
jane.dev@example.com | +1 (415) 555-0199
linkedin.com/in/janedev  github.com/janedev  https://janedev.io

EXPERIENCE
Senior Software Engineer, Acme Corp (2019 - present)
"""


def test_extract_contact_basics():
    c = extract_contact(RESUME)
    assert c["email"] == "jane.dev@example.com"
    assert "415" in c["phone"]
    assert c["linkedin"].endswith("janedev")
    assert c["github"].endswith("janedev")
    assert c["portfolio"] == "https://janedev.io"


def test_extract_profile_fields_without_llm_uses_regex():
    fields = extract_profile_fields(RESUME, llm=None)
    assert fields["email"] == "jane.dev@example.com"
    assert "linkedin" in fields and "github" in fields
    # No LLM => name/title are not guessed.
    assert "full_name" not in fields


def test_regex_overrides_llm_contact():
    class FakeLLM:
        def chat_json(self, system, user):
            return {
                "full_name": "Jane Q. Developer",
                "email": "wrong@bad.com",          # should be overridden by regex
                "current_title": "Senior Software Engineer",
                "total_years": 6,
            }

    fields = extract_profile_fields(RESUME, llm=FakeLLM())
    assert fields["full_name"] == "Jane Q. Developer"        # from LLM
    assert fields["current_title"] == "Senior Software Engineer"
    assert fields["total_years"] == 6
    assert fields["email"] == "jane.dev@example.com"          # regex wins
