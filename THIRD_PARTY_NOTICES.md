# Third-party notices

Dynamical source code is licensed under the Apache License 2.0 (see `LICENSE`).
The redistributed third-party materials below keep their own licenses and
attributions. Machine-readable per-file provenance, hashes, and license
evidence live in `dynamical/bundle/reference-lab/source-lock.json`.

## AC SDL1 electrodeposition platform geometry

The AC SDL1 electrodeposition cell geometry (cartridges, racks, tools,
electrodes, and ultrasonic mount) is redistributed as tessellated USD meshes
under `dynamical/bundle/reference-lab/assets/`:

- Creator: Nis Fisker-Bødker
- Archive: Zenodo record 15575908, version 0.0.2,
  DOI [10.5281/zenodo.15575908](https://doi.org/10.5281/zenodo.15575908)
  (`AccelerationConsortium/SDL1_OpenTron_electrodeposition`, git commit
  `2c5a911778fcd1adc6fa67e8629b7404425195cb`)
- License: CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/), as asserted by the Zenodo
  deposit record metadata. The archive itself carries no license text; the
  upstream GitHub repository later added an MIT license (Technical University
  of Denmark, 2025) that postdates tag 0.0.2. All three signals are recorded,
  unresolved, in the source lock's `conflict_notes`.

Changes made by Dynamical: upstream STEP sources were tessellated to USD meshes at a
recorded tolerance for execution visualization and collision (not metrology);
file names were normalized. No semantic changes to the geometry.

## Opentrons OT-2

The Opentrons OT-2 body and tiprack have no admitted source geometry: the
`Opentrons/ot2` repository at commit
`ef9ede131ed1d64daf9a0df5b2140a0a8e56b632` carries no license (verified: no
LICENSE file, no license or copyright string in its tree, GitHub license API
returns 404).
Dynamical redistributes no Opentrons-derived geometry; the OT-2 body and
tiprack appear in compiled stages only as machine-labelled
`execution_visualization_primitive` proxies.

## FastCat physical dataset

The frozen FastCat OER prediction table and calibration evidence derive from
*Multimetallic OER Catalyst Discovery Dataset from Autonomous Robotic Synthesis
and Electrochemical Characterization*, version 1 (2025-06-27), by Nis
Fisker-Bødker, Jin Hyun Chang, Enzo Raffaele Moretti, and Tejs Vegge.
DOI [10.11583/DTU.28494185.v1](https://doi.org/10.11583/DTU.28494185.v1).

Copyright (c) Technical University of Denmark, 2025. The DTU dataset record
identifies the license as MIT; its README and plotting scripts carry the DTU
copyright notice. This is a different platform from AC SDL1. Dynamical authored
the fitted predictor and frozen prediction table; neither is AC model code.
The raw dataset is not redistributed.

```
MIT License

Copyright (c) Technical University of Denmark, 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Non-endorsement

No endorsement is implied by the Acceleration Consortium, the Technical
University of Denmark, NIST, Caltech, NVIDIA, Opentrons, or Admiral
Instruments.
