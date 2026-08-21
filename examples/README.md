# Examples

Install `dynamical-cli`. A provider supplies one scientific operation. A
simulator provider supplies software evidence. It does not supply physical
evidence.

A calibrated twin is a provider that passed a declared calibration test within
set limits. `HOLD` means that Dynamical stopped because required evidence or
authority is missing.

Clone the repository if you do not have the example files:

```bash
git clone --depth 1 https://github.com/Dynamical-Systems-Research/dynamical-cli.git
cd dynamical-cli/examples
```

Run each example from its directory:

| Example | Purpose | Evidence and authority |
| --- | --- | --- |
| [Quickstart](quickstart/) | Run the smallest complete campaign. | Simulator evidence only. It has no hardware authority. |
| [FastCat OER](fastcat-oer/) | Compare a bounded public set of nine catalyst compositions. | Only the OER result uses the `ac-oer-twin` calibrated twin. The other operations use simulators. It has no hardware authority. |
| [Provider onboarding](provider-onboarding/) | Inspect a provider proposal. | The proposal stays pending. Composition returns `HOLD`. |

The wheel supplies the runtime and the installed authority records. The
repository supplies the example inputs. The examples do not add operations to
the installed capability list.
