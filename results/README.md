# Preliminary automated FDK results

These are automated predictions, not verified FDK measurements. The detected kernel count is the number of retained detections; fragments, duplicates, or non-wheat objects may be included. FDK percentage = 100 × automated FDK calls / retained detections. Non-wheat flags do not remove detections from this denominator.

A seven-image visual spot check across Illinois, Nebraska and SDSU found recurring partial masks and some missed kernels. Mask errors may affect both counts and classification features. Expert FDK labels were not available for that review. Do not interpret these results as independently validated measurements.

Full outlined PNGs use Git LFS. Red means predicted FDK; green means predicted healthy. Colors are predictions, not reference labels. Run `git lfs pull` after cloning to download the images.

`image_scores.csv` contains the numerical results. `input_manifest.csv` defines the 420-image batch. `provenance.json` records source-image and model hashes. Missing rows are unfinished or failed jobs, not zero scores. Local intermediate files and mask archives are excluded.

Completed results currently published: **130 / 420**.

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
| CSU_IL_41 | UIUC | 496 | 38.71 | [View](outlined/CSU_IL_41.png) |
| CSU_IL_42 | UIUC | 397 | 41.56 | [View](outlined/CSU_IL_42.png) |
| CSU_IL_43 | UIUC | 526 | 31.75 | [View](outlined/CSU_IL_43.png) |
| CSU_IL_44 | UIUC | 376 | 26.06 | [View](outlined/CSU_IL_44.png) |
| CSU_IL_45 | UIUC | 468 | 17.31 | [View](outlined/CSU_IL_45.png) |
| CSU_IL_46 | UIUC | 355 | 24.23 | [View](outlined/CSU_IL_46.png) |
| CSU_IL_47 | UIUC | 409 | 35.45 | [View](outlined/CSU_IL_47.png) |
| CSU_IL_48 | UIUC | 447 | 32.66 | [View](outlined/CSU_IL_48.png) |
| CSU_IL_49 | UIUC | 441 | 19.27 | [View](outlined/CSU_IL_49.png) |
| CSU_IL_50 | UIUC | 463 | 50.32 | [View](outlined/CSU_IL_50.png) |
| CSU_IL_51 | UIUC | 414 | 10.87 | [View](outlined/CSU_IL_51.png) |
| CSU_IL_52 | UIUC | 357 | 18.77 | [View](outlined/CSU_IL_52.png) |
| CSU_IL_53 | UIUC | 445 | 18.20 | [View](outlined/CSU_IL_53.png) |
| CSU_IL_54 | UIUC | 363 | 11.02 | [View](outlined/CSU_IL_54.png) |
| CSU_IL_55 | UIUC | 411 | 19.22 | [View](outlined/CSU_IL_55.png) |
| CSU_IL_56 | UIUC | 370 | 27.57 | [View](outlined/CSU_IL_56.png) |
| CSU_IL_57 | UIUC | 420 | 24.05 | [View](outlined/CSU_IL_57.png) |
| CSU_IL_58 | UIUC | 446 | 22.20 | [View](outlined/CSU_IL_58.png) |
| CSU_IL_59 | UIUC | 363 | 26.17 | [View](outlined/CSU_IL_59.png) |
| CSU_IL_60 | UIUC | 438 | 27.17 | [View](outlined/CSU_IL_60.png) |
| CSU_IL_61 | UIUC | 416 | 52.16 | [View](outlined/CSU_IL_61.png) |
| CSU_IL_62 | UIUC | 439 | 40.55 | [View](outlined/CSU_IL_62.png) |
| CSU_IL_63 | UIUC | 432 | 39.35 | [View](outlined/CSU_IL_63.png) |
| CSU_IL_64 | UIUC | 421 | 28.27 | [View](outlined/CSU_IL_64.png) |
| CSU_IL_65 | UIUC | 293 | 20.48 | [View](outlined/CSU_IL_65.png) |
| CSU_IL_66 | UIUC | 454 | 35.90 | [View](outlined/CSU_IL_66.png) |
| CSU_IL_67 | UIUC | 371 | 49.06 | [View](outlined/CSU_IL_67.png) |
| CSU_IL_68 | UIUC | 417 | 8.63 | [View](outlined/CSU_IL_68.png) |
| CSU_IL_69 | UIUC | 363 | 7.44 | [View](outlined/CSU_IL_69.png) |
| CSU_IL_70 | UIUC | 371 | 19.41 | [View](outlined/CSU_IL_70.png) |
| CSU_IL_71 | UIUC | 396 | 52.78 | [View](outlined/CSU_IL_71.png) |
| CSU_IL_72 | UIUC | 360 | 26.11 | [View](outlined/CSU_IL_72.png) |
| CSU_IL_73 | UIUC | 361 | 12.47 | [View](outlined/CSU_IL_73.png) |
| CSU_IL_74 | UIUC | 487 | 33.47 | [View](outlined/CSU_IL_74.png) |
| CSU_IL_75 | UIUC | 411 | 30.90 | [View](outlined/CSU_IL_75.png) |
| CSU_IL_76 | UIUC | 397 | 35.26 | [View](outlined/CSU_IL_76.png) |
| CSU_IL_77 | UIUC | 382 | 19.37 | [View](outlined/CSU_IL_77.png) |
| CSU_IL_78 | UIUC | 377 | 8.49 | [View](outlined/CSU_IL_78.png) |
| CSU_IL_79 | UIUC | 341 | 6.45 | [View](outlined/CSU_IL_79.png) |
| CSU_IL_80 | UIUC | 390 | 34.10 | [View](outlined/CSU_IL_80.png) |
| CSU_IL_81 | UIUC | 274 | 22.99 | [View](outlined/CSU_IL_81.png) |
| CSU_IL_82 | UIUC | 447 | 9.84 | [View](outlined/CSU_IL_82.png) |
| CSU_IL_83 | UIUC | 456 | 11.62 | [View](outlined/CSU_IL_83.png) |
| CSU_IL_84 | UIUC | 408 | 15.69 | [View](outlined/CSU_IL_84.png) |
| CSU_IL_85 | UIUC | 409 | 10.51 | [View](outlined/CSU_IL_85.png) |
| CSU_IL_86 | UIUC | 472 | 13.98 | [View](outlined/CSU_IL_86.png) |
| CSU_IL_87 | UIUC | 472 | 17.37 | [View](outlined/CSU_IL_87.png) |
| CSU_IL_88 | UIUC | 458 | 15.07 | [View](outlined/CSU_IL_88.png) |
| CSU_IL_89 | UIUC | 426 | 26.53 | [View](outlined/CSU_IL_89.png) |
| CSU_IL_90 | UIUC | 473 | 23.26 | [View](outlined/CSU_IL_90.png) |
| CSU_IL_91 | UIUC | 416 | 10.58 | [View](outlined/CSU_IL_91.png) |
| CSU_IL_92 | UIUC | 480 | 18.33 | [View](outlined/CSU_IL_92.png) |
| CSU_IL_93 | UIUC | 481 | 40.12 | [View](outlined/CSU_IL_93.png) |
| CSU_IL_94 | UIUC | 460 | 48.91 | [View](outlined/CSU_IL_94.png) |
| CSU_IL_95 | UIUC | 385 | 48.57 | [View](outlined/CSU_IL_95.png) |
| CSU_IL_96 | UIUC | 380 | 8.68 | [View](outlined/CSU_IL_96.png) |
| CSU_IL_97 | UIUC | 413 | 15.01 | [View](outlined/CSU_IL_97.png) |
| CSU_IL_98 | UIUC | 467 | 15.42 | [View](outlined/CSU_IL_98.png) |
| CSU_IL_99 | UIUC | 403 | 8.93 | [View](outlined/CSU_IL_99.png) |
| CSU_IL_100 | UIUC | 368 | 35.87 | [View](outlined/CSU_IL_100.png) |
| CSU_IL_101 | UIUC | 368 | 32.61 | [View](outlined/CSU_IL_101.png) |
| CSU_IL_102 | UIUC | 450 | 27.78 | [View](outlined/CSU_IL_102.png) |
| CSU_IL_103 | UIUC | 426 | 15.26 | [View](outlined/CSU_IL_103.png) |
| CSU_IL_104 | UIUC | 407 | 22.60 | [View](outlined/CSU_IL_104.png) |
| CSU_IL_105 | UIUC | 428 | 60.75 | [View](outlined/CSU_IL_105.png) |
| CSU_IL_106 | UIUC | 365 | 43.29 | [View](outlined/CSU_IL_106.png) |
| CSU_IL_107 | UIUC | 366 | 26.78 | [View](outlined/CSU_IL_107.png) |
| CSU_IL_108 | UIUC | 386 | 30.83 | [View](outlined/CSU_IL_108.png) |
| CSU_IL_109 | UIUC | 390 | 38.21 | [View](outlined/CSU_IL_109.png) |
| CSU_IL_110 | UIUC | 395 | 12.91 | [View](outlined/CSU_IL_110.png) |
| CSU_IL_111 | UIUC | 338 | 18.05 | [View](outlined/CSU_IL_111.png) |
| CSU_IL_112 | UIUC | 393 | 37.66 | [View](outlined/CSU_IL_112.png) |
| CSU_IL_113 | UIUC | 377 | 24.93 | [View](outlined/CSU_IL_113.png) |
| CSU_IL_114 | UIUC | 394 | 30.20 | [View](outlined/CSU_IL_114.png) |
| CSU_IL_115 | UIUC | 321 | 12.77 | [View](outlined/CSU_IL_115.png) |
| CSU_IL_116 | UIUC | 361 | 18.01 | [View](outlined/CSU_IL_116.png) |
| CSU_IL_117 | UIUC | 312 | 20.51 | [View](outlined/CSU_IL_117.png) |
| CSU_IL_118 | UIUC | 378 | 34.92 | [View](outlined/CSU_IL_118.png) |
| CSU_IL_119 | UIUC | 384 | 27.08 | [View](outlined/CSU_IL_119.png) |
| CSU_IL_120 | UIUC | 430 | 19.07 | [View](outlined/CSU_IL_120.png) |
| CSU_IL_121 | UIUC | 451 | 23.50 | [View](outlined/CSU_IL_121.png) |
| CSU_IL_122 | UIUC | 415 | 12.29 | [View](outlined/CSU_IL_122.png) |
| CSU_IL_123 | UIUC | 405 | 24.94 | [View](outlined/CSU_IL_123.png) |
| CSU_IL_124 | UIUC | 423 | 11.58 | [View](outlined/CSU_IL_124.png) |
| CSU_IL_125 | UIUC | 384 | 38.54 | [View](outlined/CSU_IL_125.png) |
| CSU_IL_126 | UIUC | 370 | 26.76 | [View](outlined/CSU_IL_126.png) |
| CSU_NE_2 | UNL | 451 | 33.92 | [View](outlined/CSU_NE_2.png) |
| CSU_NE_80 | UNL | 381 | 17.32 | [View](outlined/CSU_NE_80.png) |
| SDSU_Plot_4 | SDSU | 387 | 15.25 | [View](outlined/SDSU_Plot_4.png) |
| SDSU_Plot_80 | SDSU | 282 | 31.21 | [View](outlined/SDSU_Plot_80.png) |
