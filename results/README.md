# Preliminary automated FDK results

These are automated predictions, not verified FDK measurements. The detected kernel count is the number of retained detections; fragments, duplicates, or non-wheat objects may be included. FDK percentage = 100 × automated FDK calls / retained detections. Non-wheat flags do not remove detections from this denominator.

A seven-image visual spot check across Illinois, Nebraska and SDSU found recurring partial masks and some missed kernels. Mask errors may affect both counts and classification features. Expert FDK labels were not available for that review. Do not interpret these results as independently validated measurements.

Full outlined PNGs use Git LFS. Red means predicted FDK; green means predicted healthy. Colors are predictions, not reference labels. Run `git lfs pull` after cloning to download the images.

`image_scores.csv` contains the numerical results. `input_manifest.csv` defines the 420-image batch. `provenance.json` records source-image and model hashes. Missing rows are unfinished or failed jobs, not zero scores. Local intermediate files and mask archives are excluded.

Completed results currently published: **44 / 420**.

| Sample | Group | Detected count | Predicted FDK (%) | Outlined image |
|---|---|---:|---:|---|
| CSU_IL_1 | UIUC | 313 | 33.87 | [View](outlined/CSU_IL_1.png) |
| CSU_IL_2 | UIUC | 407 | 22.11 | [View](outlined/CSU_IL_2.png) |
| CSU_IL_3 | UIUC | 387 | 8.53 | [View](outlined/CSU_IL_3.png) |
| CSU_IL_4 | UIUC | 469 | 18.34 | [View](outlined/CSU_IL_4.png) |
| CSU_IL_5 | UIUC | 503 | 19.68 | [View](outlined/CSU_IL_5.png) |
| CSU_IL_6 | UIUC | 396 | 24.24 | [View](outlined/CSU_IL_6.png) |
| CSU_IL_7 | UIUC | 335 | 34.63 | [View](outlined/CSU_IL_7.png) |
| CSU_IL_8 | UIUC | 398 | 23.12 | [View](outlined/CSU_IL_8.png) |
| CSU_IL_9 | UIUC | 420 | 28.10 | [View](outlined/CSU_IL_9.png) |
| CSU_IL_10 | UIUC | 392 | 36.73 | [View](outlined/CSU_IL_10.png) |
| CSU_IL_11 | UIUC | 390 | 32.82 | [View](outlined/CSU_IL_11.png) |
| CSU_IL_12 | UIUC | 406 | 17.98 | [View](outlined/CSU_IL_12.png) |
| CSU_IL_13 | UIUC | 421 | 16.15 | [View](outlined/CSU_IL_13.png) |
| CSU_IL_14 | UIUC | 425 | 44.71 | [View](outlined/CSU_IL_14.png) |
| CSU_IL_15 | UIUC | 425 | 20.00 | [View](outlined/CSU_IL_15.png) |
| CSU_IL_16 | UIUC | 445 | 21.57 | [View](outlined/CSU_IL_16.png) |
| CSU_IL_17 | UIUC | 409 | 26.65 | [View](outlined/CSU_IL_17.png) |
| CSU_IL_18 | UIUC | 409 | 21.03 | [View](outlined/CSU_IL_18.png) |
| CSU_IL_19 | UIUC | 375 | 30.13 | [View](outlined/CSU_IL_19.png) |
| CSU_IL_20 | UIUC | 412 | 31.07 | [View](outlined/CSU_IL_20.png) |
| CSU_IL_21 | UIUC | 403 | 38.71 | [View](outlined/CSU_IL_21.png) |
| CSU_IL_22 | UIUC | 437 | 49.43 | [View](outlined/CSU_IL_22.png) |
| CSU_IL_23 | UIUC | 462 | 19.91 | [View](outlined/CSU_IL_23.png) |
| CSU_IL_24 | UIUC | 429 | 21.68 | [View](outlined/CSU_IL_24.png) |
| CSU_IL_25 | UIUC | 394 | 31.73 | [View](outlined/CSU_IL_25.png) |
| CSU_IL_26 | UIUC | 362 | 29.56 | [View](outlined/CSU_IL_26.png) |
| CSU_IL_27 | UIUC | 308 | 43.18 | [View](outlined/CSU_IL_27.png) |
| CSU_IL_28 | UIUC | 387 | 33.07 | [View](outlined/CSU_IL_28.png) |
| CSU_IL_29 | UIUC | 423 | 15.60 | [View](outlined/CSU_IL_29.png) |
| CSU_IL_30 | UIUC | 446 | 44.84 | [View](outlined/CSU_IL_30.png) |
| CSU_IL_31 | UIUC | 344 | 46.22 | [View](outlined/CSU_IL_31.png) |
| CSU_IL_32 | UIUC | 455 | 29.01 | [View](outlined/CSU_IL_32.png) |
| CSU_IL_33 | UIUC | 375 | 33.87 | [View](outlined/CSU_IL_33.png) |
| CSU_IL_34 | UIUC | 390 | 47.95 | [View](outlined/CSU_IL_34.png) |
| CSU_IL_35 | UIUC | 346 | 19.36 | [View](outlined/CSU_IL_35.png) |
| CSU_IL_36 | UIUC | 373 | 17.16 | [View](outlined/CSU_IL_36.png) |
| CSU_IL_37 | UIUC | 435 | 38.16 | [View](outlined/CSU_IL_37.png) |
| CSU_IL_38 | UIUC | 404 | 32.67 | [View](outlined/CSU_IL_38.png) |
| CSU_IL_39 | UIUC | 361 | 23.82 | [View](outlined/CSU_IL_39.png) |
| CSU_IL_40 | UIUC | 391 | 23.53 | [View](outlined/CSU_IL_40.png) |
| CSU_NE_2 | UNL | 451 | 33.92 | [View](outlined/CSU_NE_2.png) |
| CSU_NE_80 | UNL | 381 | 17.32 | [View](outlined/CSU_NE_80.png) |
| SDSU_Plot_4 | SDSU | 387 | 15.25 | [View](outlined/SDSU_Plot_4.png) |
| SDSU_Plot_80 | SDSU | 282 | 31.21 | [View](outlined/SDSU_Plot_80.png) |
