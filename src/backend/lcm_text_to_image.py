from typing import Any
from diffusers import LCMScheduler
import torch
from backend.models.lcmdiffusion_setting import LCMDiffusionSetting
import numpy as np
from constants import DEVICE
from backend.models.lcmdiffusion_setting import LCMLora
from backend.device import is_openvino_device
from PIL import Image
from backend.openvino.pipelines import get_ov_text_to_image_pipeline, ov_load_taesd
from backend.pipelines.lcm import get_lcm_model_pipeline, load_taesd
from backend.pipelines.lcm_lora import get_lcm_lora_pipeline


class LCMTextToImage:
    def __init__(
        self,
        device: str = "cpu",
    ) -> None:
        self.pipeline = None
        self.use_openvino = False
        self.device = ""
        self.previous_model_id = None
        self.previous_use_tae_sd = False
        self.previous_use_lcm_lora = False
        self.torch_data_type = (
            torch.float32 if is_openvino_device() or DEVICE == "mps" else torch.float16
        )
        print(f"Torch datatype : {self.torch_data_type}")

    def _pipeline_to_device(self):
        print(f"Pipeline device : {DEVICE}")
        print(f"Pipeline dtype : {self.torch_data_type}")
        self.pipeline.to(
            torch_device=DEVICE,
            torch_dtype=self.torch_data_type,
        )

    def _add_freeu(self):
        pipeline_class = self.pipeline.__class__.__name__
        if pipeline_class == "StableDiffusionPipeline":
            print("Add FreeU - SD")
            self.pipeline.enable_freeu(
                s1=0.9,
                s2=0.2,
                b1=1.2,
                b2=1.4,
            )
        elif pipeline_class == "StableDiffusionXLPipeline":
            print("Add FreeU - SDXL")
            self.pipeline.enable_freeu(
                s1=0.6,
                s2=0.4,
                b1=1.1,
                b2=1.2,
            )

    def init(
        self,
        device: str = "cpu",
        lcm_diffusion_setting: LCMDiffusionSetting = LCMDiffusionSetting(),
    ) -> None:
        self.device = device
        self.use_openvino = lcm_diffusion_setting.use_openvino
        model_id = lcm_diffusion_setting.lcm_model_id
        use_local_model = lcm_diffusion_setting.use_offline_model
        use_tiny_auto_encoder = lcm_diffusion_setting.use_tiny_auto_encoder
        use_lora = lcm_diffusion_setting.use_lcm_lora
        lcm_lora: LCMLora = lcm_diffusion_setting.lcm_lora

        if (
            self.pipeline is None
            or self.previous_model_id != model_id
            or self.previous_use_tae_sd != use_tiny_auto_encoder
            or self.previous_lcm_lora_base_id != lcm_lora.base_model_id
            or self.previous_lcm_lora_id != lcm_lora.lcm_lora_id
            or self.previous_use_lcm_lora != use_lora
        ):
            if self.use_openvino and is_openvino_device():
                if self.pipeline:
                    del self.pipeline
                    self.pipeline = None

                self.pipeline = get_ov_text_to_image_pipeline(
                    model_id,
                    use_local_model,
                )

                if use_tiny_auto_encoder:
                    print("Using Tiny Auto Encoder (OpenVINO)")
                    ov_load_taesd(
                        self.pipeline,
                        use_local_model,
                    )
            else:
                if self.pipeline:
                    del self.pipeline
                    self.pipeline = None

                if use_lora:
                    print("Init LCM-LoRA pipeline")
                    self.pipeline = get_lcm_lora_pipeline(
                        lcm_lora.base_model_id,
                        lcm_lora.lcm_lora_id,
                        use_local_model,
                        torch_data_type=self.torch_data_type,
                    )
                else:
                    print("Init LCM Model pipeline")
                    self.pipeline = get_lcm_model_pipeline(
                        model_id,
                        use_local_model,
                    )

                if use_tiny_auto_encoder:
                    print("Using Tiny Auto Encoder")
                    load_taesd(
                        self.pipeline,
                        use_local_model,
                        self.torch_data_type,
                    )

                self._pipeline_to_device()

            self.previous_model_id = model_id
            self.previous_use_tae_sd = use_tiny_auto_encoder
            self.previous_lcm_lora_base_id = lcm_lora.base_model_id
            self.previous_lcm_lora_id = lcm_lora.lcm_lora_id
            self.previous_use_lcm_lora = use_lora
            print(f"Model :{model_id}")
            print(f"Pipeline : {self.pipeline}")
            self.pipeline.scheduler = LCMScheduler.from_config(
                self.pipeline.scheduler.config,
                beta_start=0.001,
                beta_end=0.01,
            )
            if use_lora:
                self._add_freeu()

    def generate(
        self,
        lcm_diffusion_setting: LCMDiffusionSetting,
        reshape: bool = False,
    ) -> Any:
        guidance_scale = lcm_diffusion_setting.guidance_scale
        if lcm_diffusion_setting.use_seed:
            cur_seed = lcm_diffusion_setting.seed
            if self.use_openvino:
                np.random.seed(cur_seed)
            else:
                torch.manual_seed(cur_seed)

        if lcm_diffusion_setting.use_openvino and is_openvino_device():
            print("Using OpenVINO")
            if reshape:
                print("Reshape and compile")
                self.pipeline.reshape(
                    batch_size=-1,
                    height=lcm_diffusion_setting.image_height,
                    width=lcm_diffusion_setting.image_width,
                    num_images_per_prompt=lcm_diffusion_setting.number_of_images,
                )
                self.pipeline.compile()

        if not lcm_diffusion_setting.use_safety_checker:
            self.pipeline.safety_checker = None

        if (
            not lcm_diffusion_setting.use_lcm_lora
            and not lcm_diffusion_setting.use_openvino
            and lcm_diffusion_setting.guidance_scale != 1.0
        ):
            print("Not using LCM-LoRA so setting guidance_scale 1.0")
            guidance_scale = 1.0

        init_image = Image.open(
            r"F:\dev\push\faster\fastsdcpu\results\57540872-4f62-46b7-b6ef-3a3f10683763-1.png"
        )
        if lcm_diffusion_setting.use_openvino:
            # result_images = self.img_to_img_pipeline(
            #     image=init_image,
            #     strength=0.8,
            #     prompt=lcm_diffusion_setting.prompt,
            #     negative_prompt=lcm_diffusion_setting.negative_prompt,
            #     num_inference_steps=lcm_diffusion_setting.inference_steps,
            #     guidance_scale=guidance_scale,
            #     num_images_per_prompt=lcm_diffusion_setting.number_of_images,
            # ).images
            result_images = self.pipeline(
                prompt=lcm_diffusion_setting.prompt,
                negative_prompt=lcm_diffusion_setting.negative_prompt,
                num_inference_steps=lcm_diffusion_setting.inference_steps,
                guidance_scale=guidance_scale,
                width=lcm_diffusion_setting.image_width,
                height=lcm_diffusion_setting.image_height,
                num_images_per_prompt=lcm_diffusion_setting.number_of_images,
            ).images
        else:
            # result_images = self.img_to_img_pipeline(
            #     image=init_image,
            #     strength=0.5,
            #     prompt=lcm_diffusion_setting.prompt,
            #     negative_prompt=lcm_diffusion_setting.negative_prompt,
            #     num_inference_steps=lcm_diffusion_setting.inference_steps,
            #     guidance_scale=guidance_scale,
            #     width=lcm_diffusion_setting.image_width,
            #     height=lcm_diffusion_setting.image_height,
            #     num_images_per_prompt=lcm_diffusion_setting.number_of_images,
            # ).images
            result_images = self.pipeline(
                prompt=lcm_diffusion_setting.prompt,
                negative_prompt=lcm_diffusion_setting.negative_prompt,
                num_inference_steps=lcm_diffusion_setting.inference_steps,
                guidance_scale=guidance_scale,
                width=lcm_diffusion_setting.image_width,
                height=lcm_diffusion_setting.image_height,
                num_images_per_prompt=lcm_diffusion_setting.number_of_images,
            ).images

        return result_images
