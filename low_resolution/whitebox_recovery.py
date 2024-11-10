from engine import tune_specific_gan, tune_general_gan
from utils import *
from evaluation import evaluate_results, write_precision_list
from pathlib import Path
import torch
import os
from attack import GMI_inversion, KED_inversion
from argparse import ArgumentParser
from copy import deepcopy

torch.manual_seed(42)

parser = ArgumentParser(description='Inversion')
parser.add_argument('--configs', type=str, default='./config/celeba/attacking/celeba.json')
parser.add_argument('--exp_name',
                    default="baseline_id0-99",
                    type=str,
                    help='Directory to save output files (default: None)')
parser.add_argument('--iterations', type=int, default=1200, help='Description of iterations')
parser.add_argument('--num_round', type=int, default=1, help='Description of number of round')
parser.add_argument('--num_epoch', type=int, default=10, help='Description of epoch of GAN fine-tuning')
parser.add_argument('--num_candidates', type=int, default=1000, help='Description of number of candidates')
parser.add_argument('--target_classes', type=str, default='0-100', help='Description of target classes')

args = parser.parse_args()

device = 'cuda' if torch.cuda.is_available() else 'cpu'

def init_attack_args(cfg):
    if cfg["attack"]["method"] =='kedmi':
        args.improved_flag = True
        args.clipz = True
        args.num_seeds = 1
    else:
        args.improved_flag = False
        args.clipz = False
        args.num_seeds = 5

    if cfg["attack"]["variant"] == 'logit' or cfg["attack"]["variant"] == 'lomma':
        args.loss = 'logit_loss'
    else:
        args.loss = 'ce_loss'

    if cfg["attack"]["variant"] == 'aug' or cfg["attack"]["variant"] == 'lomma':
        args.classid = '0,1,2,3'
    else:
        args.classid = '0'


def whitebox_attack(target_model, z, G, D, E, targets_single_id, used_loss, iterations=2400, round_num=0):
    save_dir = f"{prefix}/{current_time}/{target_id:03d}"
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    if round_num == 0:
        final_z_path = f"{prefix}/final_z/baseline_{target_id:03d}.pt"
    else:
        final_z_path = f"{prefix}/final_z/round{round_num}_{target_id:03d}.pt"

    if os.path.exists(final_z_path):
        print(f"Load data from: {final_z_path}.")
        mi_time = 0
        opt_z = torch.load(final_z_path)
    else:
        print(f"File {final_z_path} does not exist, skipping load.")
        mi_start_time = time.time()
        if args.improved_flag:
            opt_z = GMI_inversion(G, D, target_model, E, targets_single_id[:batch_size], batch_size,
                                         num_candidates,
                                         used_loss=used_loss,
                                         fea_mean=fea_mean,
                                         fea_logvar=fea_logvar,
                                         iter_times=iterations,
                                         improved=args.improved_flag,
                                         lam=cfg["attack"]["lam"])
        else:
            opt_z = KED_inversion(G, D, target_model, E, batch_size, z, targets_single_id,
                                    used_loss=used_loss,
                                    fea_mean=fea_mean,
                                    fea_logvar=fea_logvar,
                                    iter_times=iterations,
                                    improved=args.improved_flag,
                                    lam=cfg["attack"]["lam"])

        mi_time = time.time() - mi_start_time

    start_time = time.time()

    final_z, final_targets = perform_final_selection(
        opt_z,
        G,
        targets_single_id,
        target_model[0],
        samples_per_target=num_candidates,
        device=device,
        batch_size=batch_size,
    )
    # no selection
    # final_z, final_targets = opt_z, targets_single_id
    selection_time = time.time() - start_time

    if round_num == 0:
        final_z_path = f"{prefix}/final_z/baseline_{target_id:03d}.pt"
    else:
        final_z_path = f"{prefix}/final_z/round{round_num}_{target_id:03d}.pt"
    torch.save(final_z.detach(), final_z_path)

    # Compute attack accuracy with evaluation model on all generated samples
    evaluate_results(E, G, batch_size, round_num, current_time, prefix, final_z, final_targets, trainset,
                     targets_single_id, save_dir)

    return final_z, final_targets, [mi_time, selection_time]


if __name__ == "__main__":
    cfg = load_json(json_file=args.configs)
    init_attack_args(cfg=cfg)

    attack_method = cfg["attack"]["method"]

    # Save dir
    if args.improved_flag == True:
        prefix = os.path.join(cfg["root_path"], "kedmi")
    else:
        prefix = os.path.join(cfg["root_path"], attack_method)

    save_folder = os.path.join("{}_{}".format(cfg["dataset"]["name"], cfg["dataset"]["model_name"]),
                               cfg["attack"]["variant"])
    prefix = os.path.join(prefix, save_folder)
    save_dir = os.path.join(prefix, "latent")
    save_img_dir = os.path.join(prefix, "imgs_{}".format(cfg["attack"]["variant"]))
    args.log_path = os.path.join(prefix, "invertion_logs")

    train_file = cfg['dataset']['train_file_path']
    print("load training data!")
    trainset, trainloader = init_dataloader(cfg, train_file, mode="train")

    # Load models
    targetnets, E, G, D, n_classes, fea_mean, fea_logvar = get_attack_model(args, cfg)
    original_G = deepcopy(G)
    original_D = deepcopy(D)

    num_candidates = args.num_candidates
    samples_per_target = args.num_candidates // 2
    target_classes = args.target_classes
    start, end = map(int, target_classes.split('-'))
    targets = torch.tensor([i for i in range(start, end)])
    targets = torch.repeat_interleave(targets, num_candidates)
    targets = targets.to(device)
    batch_size = 100

    current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
    current_time = current_time + '_' + args.exp_name if args.exp_name is not None else current_time
    dataset_name = cfg['dataset']['name']
    model_name = cfg['dataset']['model_name']
    z_dim = cfg['attack']['z_dim']

    if args.improved_flag:
        mode = "specific"
    else:
        mode = "general"

    iterations = args.iterations
    num_round = args.num_round

    for target_id in sorted(list(set(targets.tolist()))):
        G = deepcopy(original_G)
        D = deepcopy(original_D)
        for round in range(num_round):
            print(f"\nAttack target class: [{target_id}] round number: [{round}]")
            targets_single_id = targets[torch.where(targets == target_id)[0]].to(device)

            if attack_method == "gmi" or attack_method == "kedmi":
                z = torch.randn(len(targets_single_id), 100).to(device).float()
                final_z, final_targets, time_list = whitebox_attack(targetnets, z, G, D, E, targets_single_id,
                                                                 used_loss=args.loss,
                                                                 iterations=iterations,
                                                                 round_num=round)
            else:
                print("Attack method does not match this python program.")
                break

            print(f"Select a total of {samples_per_target} images from {num_candidates} images for the target classes {target_id}.\n")
            selected_z = final_z[:samples_per_target]
            selected_targets = final_z[:samples_per_target]

            if round < num_round - 1 :
                print("Start GAN fine-tuning.")

                start_time = time.time()
                json_path = f"./config/celeba/training_GAN/{mode}_gan/{dataset_name}.json"
                with open(json_path, 'r') as f:
                    config = json.load(f)

                if args.improved_flag:
                    G, D = tune_specific_gan(config, G, D, targetnets[0], selected_z, epochs=args.num_epoch)
                else:
                    G, D = tune_general_gan(config, G, D, selected_z, epochs=args.num_epoch)

                tune_time = time.time() - start_time

                time_cost_list = [['target', 'mi', 'selection', 'tune_time'],
                                [target_id, time_list[0], time_list[1], tune_time]]

                _ = write_precision_list(
                    f'{prefix}/{current_time}/time_cost_r{round + 1}',
                    time_cost_list
                )
            else:
                print("Final round reached, GAN fine-tuning skipped.")