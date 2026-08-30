"""Tests for Micro-Agent Skills and Capability Contract."""

from micro_agent.skills import CapabilityContract, SkillDefinition


class TestSkillDefinition:
    """Test skill definition."""

    def test_basic_skill(self):
        skill = SkillDefinition(id="check-eligibility", name="Check Eligibility")
        assert skill.id == "check-eligibility"
        assert skill.name == "Check Eligibility"
        assert skill.tags == []

    def test_full_skill(self):
        skill = SkillDefinition(
            id="submit-renewal",
            name="Submit Renewal",
            description="Submit a renewal application.",
            tags=["residency", "submission"],
        )
        assert "residency" in skill.tags
        assert skill.description == "Submit a renewal application."


class TestCapabilityContract:
    """Test capability contract."""

    def test_empty_contract(self):
        contract = CapabilityContract()
        assert contract.skills == []
        assert contract.tools == []

    def test_has_skill(self):
        skills = [
            SkillDefinition(id="a", name="A"),
            SkillDefinition(id="b", name="B"),
        ]
        contract = CapabilityContract(skills=skills)
        assert contract.has_skill("a") is True
        assert contract.has_skill("c") is False

    def test_find_skill(self):
        skills = [
            SkillDefinition(id="check", name="Check"),
            SkillDefinition(id="submit", name="Submit"),
        ]
        contract = CapabilityContract(skills=skills)
        found = contract.find_skill("submit")
        assert found is not None
        assert found.name == "Submit"

    def test_find_skill_not_found(self):
        contract = CapabilityContract()
        assert contract.find_skill("missing") is None

    def test_skills_by_tag(self):
        skills = [
            SkillDefinition(id="a", name="A", tags=["residency"]),
            SkillDefinition(id="b", name="B", tags=["payment"]),
            SkillDefinition(id="c", name="C", tags=["residency", "eligibility"]),
        ]
        contract = CapabilityContract(skills=skills)
        residency_skills = contract.skills_by_tag("residency")
        assert len(residency_skills) == 2

    def test_tools_and_mcp_servers(self):
        contract = CapabilityContract(
            tools=["echo", "check"],
            mcp_servers=["residency-services"],
        )
        assert len(contract.tools) == 2
        assert len(contract.mcp_servers) == 1
