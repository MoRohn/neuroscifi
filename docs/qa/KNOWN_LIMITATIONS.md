# Known limitations

These are the limits of NeuroSciFi 0.7.0 as stated in the repository's README until 2026-11-27, moved here unchanged.
Release status: [0.7.0 release acceptance](V0.7.0_RELEASE_ACCEPTANCE.md).

- **The guard is lexical, not semantic.** It compares words, not meaning: a faithful paraphrase is refused, and some changes of meaning still pass (a bound of time, one name swapped for another in a fused sentence). A hostile page can have its own weak claim shown word for word with the code's hedge, or a faithful sentence refused. In right-to-left text, a line with direction marks is read but never cited. Open items: [Engineering plan](../acceleration/ENGINEERING_PLAN.md).
- **Contradictions in other words** were found for 45 of 50 on a fresh held-out set, values in other units and stated aliases included, with 1 false pair of 28. Found pairs are asked, never decided. Four bare digits ("5000 residents") are read as a year, and numbers written as words are not read.
- **News and sources.** A news page that needs more than 100 sub-requests renders incomplete, and answers about current events are usually single-source. DOI records all resolve through doi.org, so distinct papers fetched that way count as one source group.
- **Voice on real hardware rests on the owner's Voice check.** Public corpora gave a Whisper WER of 2.1 % on clean speech and 18.6 % on accented conversational English; echo, rooms and phones were simulated. Barge-in favours a reliable spoken "stop" over false stops from loud music; without browser echo cancellation NeuroSciFi's own voice interrupts it; commands are English only ([voice validation](../voice/VOICE_VALIDATION.md)).
- **General discovery is thin without a Brave key**, and Brave has offline tests only. Browser impersonation will not be enabled.
- **PersonaPlex is not real-time** on GB10 (99–152 ms per 80 ms frame) and stays an opt-in experiment that never answers a question.
- **Owner-side checks not run here:** root-only hardening and systemd/polkit acting on the installed units; the CA, Pocket and push on physical phones; a real remote provider; days of wall-clock autonomy; a person using a screen reader. Calibration's evidence is simulated: no individual has settled predictions yet.
- **Wave 1's cognitive mechanisms are measured on synthetic, offline worlds only.** No answer confidence is shown (not enough development data); predictive regulation is a simulated-plant result; nothing was measured on the open web, a live model or real hardware ([Wave 1](../genesis/WAVE1.md), "What stays open").
- **No worker federation.** Nodes submit, own and hand off research, but the Spark that runs `nsf web work` is the only executing node.
