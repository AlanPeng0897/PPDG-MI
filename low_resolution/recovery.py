from engine import tune_specific_gan, tune_general_gan, tune_cgan
from utils import *
from evaluation import evaluate_results, write_precision_list
from pathlib import Path
import torch
import os
from attack import GMI_inversion, KED_inversion, RLB_inversion, BREP_inversion, PLG_inversion
from argparse import ArgumentParser
from copy import deepcopy
from SAC import Agent

torch.manual_seed(9)

parser = ArgumentParser(description='Inversion')
parser.add_argument('--configs', type=str, default='./config/celeba/attacking/celeba.json')
parser.add_argument('--exp_name',
                    default="baseline_id0-99",
                    type=str,
                    help='Directory to save output files (default: None)')
parser.add_argument('--iterations', type=int, default=1200, help='Description of iterations')
parser.add_argument('--num_round', type=int, default=1, help='Description of number of round')
parser.add_argument('--num_candidates', type=int, default=1000, help='Description of number of candidates')
parser.add_argument('--target_classes', type=str, default='0-100', help='Description of target classes')

args = parser.parse_args()
print("1234567")

parser.add_argument('--private_data_name', type=str, default='celeba', help='celeba | ffhq | facescrub')
parser.add_argument('--public_data_name', type=str, default='ffhq', help='celeba | ffhq | facescrub')


parser.add_argument('--alpha', type=float, default=0.2, help='weight of inv loss. default: 0.2')

# Log and Save interval configuration
parser.add_argument('--results_root', type=str, default='results',
                    help='Path to results directory. default: results')

# tune cGAN
# Generator configuration
parser.add_argument('--gen_distribution', '-gd', type=str, default='normal',
                    help='Input noise distribution: normal (default) or uniform.')

# PLG Optimizer settings
parser.add_argument('--log_interval', '-li', type=int, default=100,
                    help='Interval of showing losses. default: 100')
parser.add_argument('--loss_type', type=str, default='hinge',
                    help='loss function name. hinge (default) or dcgan.')
parser.add_argument('--relativistic_loss', '-relloss', default=False, action='store_true',
                    help='Apply relativistic loss or not. default: False')









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
    if cfg["attack"]["method"] =='plg':
        args.conditional_flag = True
    else:
        args.conditional_flag = False

    if cfg["attack"]["variant"] == 'logit' or cfg["attack"]["variant"] == 'lomma':
        args.loss = 'logit_loss'
    elif cfg["attack"]["variant"] == 'poincare':
        args.loss = 'poincare_loss'
    elif cfg["attack"]["variant"] == 'margin':
        args.loss = 'margin_loss'
    else:
        args.loss = 'cel'

    if cfg["attack"]["variant"] == 'aug' or cfg["attack"]["variant"] == 'lomma':
        args.classid = '0,1,2,3'
    else:
        args.classid = '0'


def white_attack(target_model, z, G, D, E, targets_single_id, used_loss, iterations=2400, round_num=0):
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
            opt_z = KED_inversion(G, D, target_model, E, targets_single_id[:batch_size], batch_size,
                                         num_candidates,
                                         used_loss=used_loss,
                                         fea_mean=fea_mean,
                                         fea_logvar=fea_logvar,
                                         iter_times=iterations,
                                         improved=args.improved_flag,
                                         lam=cfg["attack"]["lam"])
        else:
            opt_z = GMI_inversion(G, D, target_model, E, batch_size, z, targets_single_id,
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

def PLG_attack(args, G, D, target_model, E, targets_single_id, used_loss, iterations=600, round_num=1):
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
        opt_z = PLG_inversion(args, G, D, target_model, E, batch_size, targets_single_id, used_loss=used_loss, lr=args.lr, iterations=iterations)
        mi_time = time.time() - mi_start_time
        torch.save(opt_z.detach(), final_z_path)

    start_time = time.time()
    final_z, final_targets = perform_final_selection(
        opt_z,
        G,
        targets_single_id,
        target_model,
        samples_per_target=num_candidates,
        device=device,
        batch_size=batch_size,
    )
    selection_time = time.time() - start_time

    print(f'Selected a total of {final_z.shape[0]} final images out of {opt_z.shape[0]} images',
          f'of target classes {set(final_targets.cpu().tolist())}.')

    # Compute attack accuracy with evaluation model on all generated samples
    evaluate_results(E, G, batch_size, round_num, current_time, prefix, final_z, final_targets, trainset,
                     targets_single_id, save_dir)

    return final_z, final_targets, [mi_time, selection_time]

def RLB_attack(agent, G, target_model, alpha, z, max_episodes, max_step, targets_single_id, round_num=0):
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
        opt_z = RLB_inversion(agent, G, target_model, alpha, z, max_episodes, max_step,
                                targets_single_id[0])
        mi_time = time.time() - mi_start_time

    start_time = time.time()
    # final_z, final_targets = opt_z, targets_single_id
    final_z, final_targets = perform_final_selection(
        opt_z,
        G,
        targets_single_id,
        target_model,
        samples_per_target=num_candidates,
        device=device,
        batch_size=batch_size,
    )
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


def BREP_attack(attack_params, G, target_model, E, z, targets_single_id, target_id, max_iters_at_radius_before_terminate, used_loss, round_num=0):
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
        opt_z = BREP_inversion(z, target_id, targets_single_id, G, target_model, E, attack_params, used_loss,
                               used_loss, max_iters_at_radius_before_terminate, save_dir, round_num)
        mi_time = time.time() - mi_start_time

    start_time = time.time()
    # final_z, final_targets = opt_z, targets_single_id
    final_z, final_targets = perform_final_selection(
        opt_z,
        G,
        targets_single_id,
        target_model,
        samples_per_target=num_candidates,
        device=device,
        batch_size=batch_size,
    )
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

    os.makedirs(prefix, exist_ok=True)
    os.makedirs(f"{prefix}/final_z", exist_ok=True)
    os.makedirs(args.log_path, exist_ok=True)

    train_file = cfg['dataset']['train_file_path']
    print("load training data!")
    trainset, trainloader = init_dataloader(cfg, train_file, mode="train")

    # Load models
    targetnets, E, G, D, n_classes, fea_mean, fea_logvar = get_attack_model(args, cfg)
    original_G = deepcopy(G)
    original_D = deepcopy(D)

    num_candidates = args.num_candidates
    samples_per_target = args.num_candidates
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

    max_step = cfg['RLB_MI']['max_step']
    seed = cfg['RLB_MI']['seed']
    alpha = cfg['RLB_MI']['alpha']
    max_episodes = args.iterations

    z_dim = cfg['BREP_MI']['z_dim']
    batch_dim_for_initial_points = cfg['BREP_MI']['batch_dim_for_initial_points']
    point_clamp_min = cfg['BREP_MI']['point_clamp_min']
    point_clamp_max = cfg['BREP_MI']['point_clamp_max']
    max_iters_at_radius_before_terminate = args.iterations

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

            if attack_method == "brep":
                toogle_grad(G, False)
                toogle_grad(D, False)

                z = gen_initial_points_targeted(batch_dim_for_initial_points,
                                                G,
                                                targetnets[0],
                                                point_clamp_min,
                                                point_clamp_max,
                                                z_dim,
                                                num_candidates,
                                                target_id)

                final_z, final_targets, time_list = BREP_attack(cfg, G, targetnets[0], E, z,
                                                                      targets_single_id, target_id, max_iters_at_radius_before_terminate,
                                                                      used_loss=args.loss,
                                                                      round_num=round)

            elif attack_method == 'rlb':
                z = torch.randn(len(targets_single_id), 100).to(device).float()
                agent = Agent(state_size=z_dim, action_size=z_dim, random_seed=seed, hidden_size=256,
                              action_prior="uniform")

                final_z, final_targets, time_list = RLB_attack(agent, G, targetnets[0], alpha, z,
                                                                 max_episodes,
                                                                 max_step, targets_single_id,
                                                                 round_num=round)
            elif attack_method == 'plg':
                final_z, final_targets, time_list = PLG_attack(args, G, D, targetnets[0], E, targets_single_id,
                                                               used_loss=args.loss,
                                                               iterations=iterations,
                                                               round_num=round)
            else:
                z = torch.randn(len(targets_single_id), 100).to(device).float()
                final_z, final_targets, time_list = white_attack(targetnets, z, G, D, E, targets_single_id,
                                                                 used_loss=args.loss,
                                                                 iterations=iterations,
                                                                 round_num=round)

            print(f"Select a total of {samples_per_target} images from {num_candidates} images for the target classes {target_id}.\n")
            selected_z = final_z[:samples_per_target]
            selected_targets = final_z[:samples_per_target]

            if round < num_round - 1 :
                print("Starting GAN fine-tuning.")

                start_time = time.time()
                json_path = f"./config/celeba/training_GAN/{mode}_gan/{dataset_name}.json"
                with open(json_path, 'r') as f:
                    config = json.load(f)

                if args.improved_flag:
                    G, D = tune_specific_gan(config, G, D, targetnets[0], selected_z, epochs=10)
                elif args.conditional_flag:
                    G, D = tune_cgan(args, config, G, D, targetnets[0], selected_z, selected_targets, epoch=500)
                else:
                    G, D = tune_general_gan(config, G, D, selected_z, epochs=10)

                tune_time = time.time() - start_time

                time_cost_list = [['target', 'mi', 'selection', 'tune_time'],
                                [target_id, time_list[0], time_list[1], tune_time]]

                _ = write_precision_list(
                    f'{prefix}/{current_time}/time_cost_r{round + 1}',
                    time_cost_list
                )
            else:
                print("Final round reached, GAN fine-tuning skipped.")