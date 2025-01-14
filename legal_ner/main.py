import os
import json
import numpy as np
from argparse import ArgumentParser
from nervaluate import Evaluator

from transformers import AutoModelForTokenClassification
from transformers import Trainer, DefaultDataCollator, TrainingArguments

from utils.dataset import LegalNERTokenDataset
from utils.utils import extract_embeddings

import spacy
nlp = spacy.load("en_core_web_sm")


############################################################
#                                                          #
#                           MAIN                           #
#                                                          #
############################################################ 
if __name__ == "__main__":

    parser = ArgumentParser(description="Training of LUKE model")
    parser.add_argument(
        "--extract_embedding",
        help="if you want to perform embeddings extraction",
        required=False,
        type=str2bool,
        default=False
    )
    parser.add_argument(
        "--ds_train_path",
        help="Path of train dataset file",
        default="/content/NLP-NER-Project/legal_ner/NER_TRAIN/NER_TRAIN_JUDGEMENT.json",
        required=False,
        type=str,
    )
    parser.add_argument(
        "--ds_train_path_defense",
        help="Path of domain shift train dataset file",
        default="/content/NLP-NER-Project/legal_ner/NER_SHIFT_TRAIN/UKGovernment_train.json",
        required=False,
        type=str,
    )
    parser.add_argument(
        "--ds_valid_path",
        help="Path of validation dataset file",
        default="/content/NLP-NER-Project/legal_ner/NER_DEV/NER_DEV_JUDGEMENT.json",
        required=False,
        type=str,
    )
    parser.add_argument(
        "--ds_valid_path_defense",
        help="Path of domain shift validation dataset file",
        default="/content/NLP-NER-Project/legal_ner/NER_SHIFT_TEST/UKGovernment_test.json",
        required=False,
        type=str,
    )
    parser.add_argument(
        "--output_folder",
        help="Output folder",
        default="results/",
        required=False,
        type=str,
    )
    parser.add_argument(
        "--batch",
        help="Batch size",
        default=1,
        required=False,
        type=int,
    )
    parser.add_argument(
        "--num_epochs",
        help="Number of training epochs",
        default=5,
        required=False,
        type=int,
    )
    parser.add_argument(
        "--lr",
        help="Learning rate",
        default=1e-5,
        required=False,
        type=float,
    )
    parser.add_argument(
        "--weight_decay",
        help="Weight decay",
        default=0.01,
        required=False,
        type=float,
    )
    parser.add_argument(
        "--warmup_ratio",
        help="Warmup ratio",
        default=0.06,
        required=False,
        type=float,
    )
    parser.add_argument(
        "--model_checkpoint_path",
        help="Path for the checkpoint to use",
        default="/content/checkpoint-47175",
        required=False,
        type=str
    )

    args = parser.parse_args()

    ## Parameters
    extract_embedding = args.extract_embedding
    ds_train_path = args.ds_train_path  # e.g., 'data/NER_TRAIN/NER_TRAIN_ALL.json'
    ds_train_path_defense = args.ds_train_path_defense 
    ds_valid_path = args.ds_valid_path  # e.g., 'data/NER_DEV/NER_DEV_ALL.json'
    ds_valid_path_defense = args.ds_valid_path_defense  
    output_folder = args.output_folder  # e.g., 'results/'
    batch_size = args.batch             # e.g., 256 for luke-based, 1 for bert-based
    num_epochs = args.num_epochs        # e.g., 5
    lr = args.lr                        # e.g., 1e-4 for luke-based, 1e-5 for bert-based
    weight_decay = args.weight_decay    # e.g., 0.01
    warmup_ratio = args.warmup_ratio    # e.g., 0.06
    model_checkpoint_path = args.model_checkpoint_path 
    
    ## Define the labels
    original_label_list = [
        "COURT",
        "PETITIONER",
        "RESPONDENT",
        "JUDGE",
        "DATE",
        "ORG",
        "GPE",
        "STATUTE",
        "PROVISION",
        "PRECEDENT",
        "CASE_NUMBER",
        "WITNESS",
        "OTHER_PERSON",
        "LAWYER"
    ]
    labels_list = ["B-" + l for l in original_label_list]
    labels_list += ["I-" + l for l in original_label_list]
    num_labels = len(labels_list) + 1

    original_label_list_defense = [
        "CommsIdentifier",
        "DocumentReference",
        "Frequency",
        "Location",
        "MilitaryPlatform",
        "Money",
        "Nationality",
        "Organisation",
        "Person",
        "Quantity",
        "Temporal",
        "Url",
        "Vehicle",
        "Weapon"
    ]
    
    labels_list_defense = ["B-" + l for l in original_label_list_defense]
    labels_list_defense += ["I-" + l for l in original_label_list_defense]
    num_labels_defense = len(labels_list_defense) + 1



    
    ## Compute metrics
    def compute_metrics(pred):

        # Preds
        predictions = np.argmax(pred.predictions, axis=-1)
        predictions = np.concatenate(predictions, axis=0)
        prediction_ids = [[idx_to_labels[p] if p != -100 else "O" for p in predictions]]

        # Labels
        labels = pred.label_ids
        labels = np.concatenate(labels, axis=0)
        labels_ids = [[idx_to_labels[p] if p != -100 else "O" for p in labels]]
        unique_labels = list(set([l.split("-")[-1] for l in list(set(labels_ids[0]))]))
        unique_labels.remove("O")

        # Evaluator
        evaluator = Evaluator(
            labels_ids, prediction_ids, tags=unique_labels, loader="list"
        )
        results, results_per_tag = evaluator.evaluate()

        return {
            "f1-type-match": 2
            * results["ent_type"]["precision"]
            * results["ent_type"]["recall"]
            / (results["ent_type"]["precision"] + results["ent_type"]["recall"] + 1e-9),
            "f1-partial": 2
            * results["partial"]["precision"]
            * results["partial"]["recall"]
            / (results["partial"]["precision"] + results["partial"]["recall"] + 1e-9),
            "f1-strict": 2
            * results["strict"]["precision"]
            * results["strict"]["recall"]
            / (results["strict"]["precision"] + results["strict"]["recall"] + 1e-9),
            "f1-exact": 2
            * results["exact"]["precision"]
            * results["exact"]["recall"]
            / (results["exact"]["precision"] + results["exact"]["recall"] + 1e-9),
        }

    ## Define the models
    model_paths = [
        "dslim/bert-large-NER",                     # ft on NER
        "Jean-Baptiste/roberta-large-ner-english",  # ft on NER
        "nlpaueb/legal-bert-base-uncased",          # ft on Legal Domain
        "saibo/legal-roberta-base",                 # ft on Legal Domain
        "nlpaueb/bert-base-uncased-eurlex",         # ft on Eurlex
        "nlpaueb/bert-base-uncased-echr",           # ft on ECHR
        "studio-ousia/luke-base",                   # LUKE base
        "studio-ousia/luke-large",                  # LUKE large
    ]

    for model_path in model_paths:

        print("MODEL: ", model_path)

        ## Define the train and test datasets
        use_roberta = False
        if "luke" in model_path or "roberta" in model_path:
            use_roberta = True

        train_ds = LegalNERTokenDataset(
            ds_train_path, 
            model_path, 
            labels_list=labels_list, 
            split="train", 
            use_roberta=use_roberta
        )

        train_defense_ds = LegalNERTokenDataset(
            ds_train_path_defense, 
            model_path, 
            labels_list=labels_list_defense, 
            split="train", 
            use_roberta=use_roberta
        )

        val_ds = LegalNERTokenDataset(
            ds_valid_path, 
            model_path, 
            labels_list=labels_list, 
            split="val", 
            use_roberta=use_roberta
        )

        val_defense_ds = LegalNERTokenDataset(
            ds_valid_path_defense, 
            model_path, 
            labels_list=labels_list_defense, 
            split="val", 
            use_roberta=use_roberta
        )


        ## Define the model
        model = AutoModelForTokenClassification.from_pretrained(
            model_checkpoint_path, # model_path
            num_labels=num_labels, 
            ignore_mismatched_sizes=True
        )

        ## Map the labels
        idx_to_labels = {v[1]: v[0] for v in train_ds.labels_to_idx.items()}

        ## Output folder
        new_output_folder = os.path.join(output_folder, 'all')
        new_output_folder = os.path.join(new_output_folder, model_path)
        if not os.path.exists(new_output_folder):
            os.makedirs(new_output_folder)

        ## Training Arguments
        training_args = TrainingArguments(
            resume_from_checkpoint=model_checkpoint_path,
            output_dir=new_output_folder,
            num_train_epochs=num_epochs,
            learning_rate=lr,
            per_device_train_batch_size=batch_size,
            per_device_eval_batch_size=batch_size,
            gradient_accumulation_steps=1,
            gradient_checkpointing=True,
            warmup_ratio=warmup_ratio,
            weight_decay=weight_decay,
            evaluation_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=False,
            save_total_limit=2,
            fp16=False,
            fp16_full_eval=False,
            metric_for_best_model="f1-strict",
            dataloader_num_workers=4,
            dataloader_pin_memory=True,
        )

        ## Collator
        data_collator = DefaultDataCollator()

        ## Trainer
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=train_ds,
            eval_dataset=val_ds,
            compute_metrics=compute_metrics,
            data_collator=data_collator,
        )

        trainer2 = Trainer(
            model=model,
            args=training_args,
            train_dataset=train_defense_ds,
            eval_dataset=val_defense_ds,
            compute_metrics=compute_metrics,
            data_collator=data_collator
        )

        ## Train the model and save it
        if extract_embedding:
            dataloader = trainer.get_train_dataloader()
            embeddings = extract_embeddings(model, dataloader, "embeddings_legal1.pt", "labels_legal1.pt")
            dataloader = trainer2.get_train_dataloader()
            embeddings2 = extract_embeddings(model, dataloader, "embeddings_def_train.pt", "labels_def_train.pt")
            dataloader = trainer2.get_eval_dataloader()
            embeddings3 = extract_embeddings(model, dataloader, "embeddings_def_val.pt", "labels_def_val.pt")
        else:
            trainer.train()
            trainer.save_model(output_folder)
            trainer.evaluate()
        
        



"""python 3.10
Example of usage:
python main.py \
    --ds_train_path data/NER_TRAIN/NER_TRAIN_ALL.json \
    --ds_valid_path data/NER_DEV/NER_DEV_ALL.json \
    --ds_train_path_defense data/NER_TRAIN/NER_TRAIN_ALL.json \
    --ds_valid_path_defense data/NER_DEV/NER_DEV_ALL.json \
    --output_folder results/ \
    --batch 256 \
    --num_epochs 5 \
    --lr 1e-4 \
    --weight_decay 0.01 \
    --warmup_ratio 0.06
"""
