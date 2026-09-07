# Examples

Install `dynamical-cli`. A provider supplies one scientific operation. A
simulator provider supplies software evidence. It does not supply physical
evidence.

A calibrated-twin admission applies to a particular provider output and its
declared calibration domain; it does not qualify an entire facility. `HOLD` means that Dynamical stopped because required evidence or
authority is missing.

Clone the repository if you do not have the example files:

```bash
git clone --depth 1 https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli/examples
```

Run each example from its directory:

| Example | Purpose | Evidence and authority |
| --- | --- | --- |
| [Quickstart](quickstart/) | Record SDL1 temperature and timed-ultrasound commands in one well. | Source-command bookkeeping only; physical responses remain unknown. |
| [FastCat OER](fastcat-oer/) | Compare a bounded public set of nine catalyst compositions. | The separate FastCat facility supplies nominal bath bookkeeping and the historically calibrated `ac-oer-twin` predictor. No hardware authority. |
| [Provider onboarding](provider-onboarding/) | Inspect a provider proposal. | The proposal stays pending. Composition returns `HOLD`. |

The wheel supplies the runtime and the installed authority records. The
repository supplies the example inputs. The examples do not add operations to
the installed capability list.

The FastCat predictor's original validation MAE was 22.2 mV; a distinct
54-composition pool evaluation gave 48.4 mV, with 43 single-run labels. Its
constant 0.104969 V half-width targets 90% coverage. See the
[FastCat example](fastcat-oer/) for the cohort and claim boundaries.
Full reference-facility calibration remains a release prerequisite.
