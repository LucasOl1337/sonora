"""HDMI output discovery when the GPU publishes only one sink at a time."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio import missing_outputs, label_sinks


def port(name, product, profile, available="yes", direction="output", extra_profiles=()):
    profiles = [profile, *extra_profiles]
    return Obj(name=name, description=name.replace("hdmi-output-", "HDMI "),
               direction=Obj(name=direction), available=Obj(name=available),
               profile_list=profiles, proplist={"device.product.name": product})


class OutputDiscoveryTests(unittest.TestCase):
    def nvidia_card(self, active="output:hdmi-stereo-extra2"):
        stereo = [
            Obj(name="output:hdmi-stereo", available=True, n_sinks=1, n_sources=0, priority=5900),
            Obj(name="output:hdmi-stereo-extra1", available=True, n_sinks=1, n_sources=0, priority=5700),
            Obj(name="output:hdmi-stereo-extra2", available=True, n_sinks=1, n_sources=0, priority=5700),
            Obj(name="output:hdmi-stereo-extra3", available=True, n_sinks=1, n_sources=0, priority=5700),
        ]
        surround = Obj(name="output:hdmi-surround-extra3", available=True, n_sinks=1, n_sources=0, priority=600)
        ports = [
            port("hdmi-output-0", "32", stereo[0]),
            port("hdmi-output-1", "VG32VQ1B", stereo[1]),
            port("hdmi-output-2", "LS27AG32x", stereo[2]),
            port("hdmi-output-3", "S85F", stereo[3], extra_profiles=(surround,)),
        ]
        active_profile = next(p for p in stereo if p.name == active)
        return Obj(index=51, name="alsa_card.pci-0000_01_00.1", profile_active=active_profile,
                   profile_list=[*stereo, surround], port_list=ports, proplist={})

    def test_s85f_is_visible_while_another_hdmi_sink_is_published(self):
        missing = missing_outputs([self.nvidia_card()], [{"card": 51, "port": "hdmi-output-2"}])
        self.assertEqual([m["title"] for m in missing], ["32", "VG32VQ1B", "S85F"])
        s85f = next(m for m in missing if m["title"] == "S85F")
        self.assertEqual(s85f["profile"], "output:hdmi-stereo-extra3")
        self.assertEqual(s85f["kind"], "missing_sink")
        self.assertEqual(s85f["port"], "hdmi-output-3")

    def test_published_hdmi_port_is_not_listed_as_missing(self):
        missing = missing_outputs([self.nvidia_card("output:hdmi-stereo-extra3")],
                                  [{"card": 51, "port": "hdmi-output-3"}])
        self.assertNotIn("S85F", [m["title"] for m in missing])

    def test_disconnected_hdmi_port_is_hidden(self):
        card = self.nvidia_card()
        card.port_list[3] = port("hdmi-output-3", "S85F", card.profile_list[3], available="no")
        missing = missing_outputs([card], [{"card": 51, "port": "hdmi-output-2"}])
        self.assertNotIn("S85F", [m["title"] for m in missing])

    def test_surround_profiles_prefer_stereo(self):
        missing = missing_outputs([self.nvidia_card()], [{"card": 51, "port": "hdmi-output-2"}])
        self.assertEqual(next(m["profile"] for m in missing if m["title"] == "S85F"), "output:hdmi-stereo-extra3")

    def test_published_hdmi_sink_uses_monitor_product_name(self):
        sinks = [{"card": 51, "port": "hdmi-output-2", "title": "AD103 High Definition Audio Controller Digital Stereo (HDMI 3)", "nick": "LS27AG32x"}]
        label_sinks(sinks, [self.nvidia_card()])
        self.assertEqual(sinks[0]["title"], "LS27AG32x")


if __name__ == "__main__":
    unittest.main()
