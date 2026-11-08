# Evaluation images

Three **synthetic** images are included so the "difficult" cases run out of the box:

| File | Case | Expected behaviour |
|---|---|---|
| `blurry.jpg` | blurry | Generic listing, condition `Unable to Determine`, no brand |
| `dark.jpg` | dark_image | Generic listing or refusal, condition `Unable to Determine` |
| `landscape.jpg` | non_product_landscape | `422 no_product_detected` |

Add your own photos (any real product photos, JPG/PNG/WEBP) under these exact names to enable the remaining cases.
Cases whose images are missing are **skipped**, never failed.

`iphone.jpg` `laptop.jpg` `furniture.jpg` `vehicle.jpg` `tool.jpg` `multiple_products.jpg`
`cluttered.jpg` `partial.jpg` `unknown_brand.jpg`

Check what is ready with `python -m scripts.run_evaluation --dry-run`.
