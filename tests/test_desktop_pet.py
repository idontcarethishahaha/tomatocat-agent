from pet_systems import PetSystems


def test_auto_walk_keeps_every_step_inside_screen(monkeypatch):
    class Pet:
        def __init__(self):
            self._x = 100
            self._y = 200
            self.moves = []
            self._facing_right = True

        def x(self):
            return self._x

        def y(self):
            return self._y

        def _clamp(self, x, y):
            return min(x, 104), y

        def move(self, x, y):
            self._x, self._y = x, y
            self.moves.append((x, y))

        def set_animation(self, *_args):
            pass

    pet = Pet()
    systems = PetSystems.__new__(PetSystems)
    systems.pet = pet
    monkeypatch.setattr("pet_systems.random.choice", lambda _values: 1)
    monkeypatch.setattr("pet_systems.random.randint", lambda _low, _high: 5)
    monkeypatch.setattr("pet_systems.QTimer.singleShot", lambda _delay, callback: callback())

    systems._start_auto_walk()

    assert pet.moves
    assert all(x <= 104 for x, _y in pet.moves)
