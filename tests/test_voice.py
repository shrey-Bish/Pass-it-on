import anthropic
import pytest

from passiton import voice
from passiton.models import Item

from .conftest import at, run

ITEM = Item(id="ti84maya", owner_id="maya", type="TI-84 Plus CE", category="calculator", persona="dramatic",
            created_at=at(7))
FACTS = {"owner": "Maya", "other": "Priya", "building": "Life Sciences A", "time": "1:20 PM", "n": 2,
         "course": "CHM 113", "window": "Thu 12:30–1:20 PM", "return_by": "5:00 PM"}


@pytest.mark.parametrize("event", sorted(voice.TEMPLATES))
@pytest.mark.parametrize("persona", ["dramatic", "chill", "wholesome"])
def test_every_template_renders_for_every_persona(event, persona):
    text = voice.template(event, ITEM.model_copy(update={"persona": persona}), FACTS)
    assert text and len(text) <= voice.MAX_LENGTH and "{" not in text


def test_a_model_reply_naming_another_student_is_rejected():
    names = {"Maya", "Priya", "Jay", "Dev"}
    assert voice.acceptable("Priya needs me at Life Sciences A at 1:20 PM!", FACTS, names)
    assert not voice.acceptable("Priya needs me at Life Sciences A at 1:20 PM, then Jay!", FACTS, names)
    assert not voice.acceptable("Priya needs me at Life Sciences A at 2:20 PM!", FACTS, names)  # wrong time
    assert not voice.acceptable("x" * 400, FACTS, names)


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error
        self.messages = self

    async def create(self, **kwargs):
        if self.error:
            raise self.error
        return type("R", (), {"content": [type("B", (), {"type": "text", "text": self.reply})()]})()


def test_the_model_voice_is_used_when_it_keeps_the_facts():
    client = FakeClient('{"text": "Darling Priya awaits at Life Sciences A at 1:20 PM. Go!"}')
    text = run(voice.say("your_dropoff", ITEM, FACTS, other_names={"Jay"}, use_model=True, client=client))
    assert text.startswith("Darling Priya")


def test_the_template_is_used_when_the_model_adds_a_name_or_is_down():
    leaky = FakeClient('{"text": "Priya awaits at Life Sciences A at 1:20 PM, and Jay was late again."}')
    text = run(voice.say("your_dropoff", ITEM, FACTS, other_names={"Jay"}, use_model=True, client=leaky))
    assert text == voice.template("your_dropoff", ITEM, FACTS)
    down = FakeClient(error=anthropic.APIConnectionError(request=None))
    assert run(voice.say("nudge", ITEM, FACTS, use_model=True, client=down)) == voice.template("nudge", ITEM, FACTS)
